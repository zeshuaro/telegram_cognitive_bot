#!/usr/bin/env python3
# coding: utf-8

import dotenv
import langdetect
import logging
import mimetypes
import operator
import os
import re
import requests
import shlex
import smtplib
import speech_recognition as sr
import time

from PIL import Image, ImageDraw, ImageFont
from subprocess import Popen, PIPE

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Updater, CommandHandler, ConversationHandler, MessageHandler, Filters, RegexHandler
from telegram.ext.dispatcher import run_async

from cognitive_cov_states import *

# Enable logging
logging.basicConfig(format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %I:%M:%S %p",
                    level=logging.INFO)
logger = logging.getLogger(__name__)

dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
dotenv.load(dotenv_path)
app_url = os.environ.get("APP_URL")
port = int(os.environ.get("PORT", "5000"))

telegram_token = os.environ.get("TELEGRAM_TOKEN_BETA") if os.environ.get("TELEGRAM_TOKEN_BETA") \
    else os.environ.get("TELEGRAM_TOKEN")
dev_tele_id = int(os.environ.get("DEV_TELE_ID"))
dev_email = os.environ.get("DEV_EMAIL") if os.environ.get("DEV_EMAIL") else "sample@email.com"
dev_email_pw = os.environ.get("DEV_EMAIL_PW")
is_email_feedback = os.environ.get("IS_EMAIL_FEEDBACK")
smtp_host = os.environ.get("SMTP_HOST")

comp_vision_token = os.environ.get("COMP_VISION_TOKEN")
comp_vision_url = os.environ.get("COMP_VISION_URL")
emotion_token = os.environ.get("EMOTION_TOKEN")
emotion_url = os.environ.get("EMOTION_URL")
bing_speech_token = os.environ.get("BING_SPEECH_TOKEN")

cognitive_image_size_limit = 4000000
download_size_limit = 20000000
upload_size_limit = 50000000

clip_art_types = {0: "non-clip-art", 1: "ambiguous", 2: "normal-clip-art", 3: "good-clip-art"}


def main():
    # Create the EventHandler and pass it your bot"s token.
    updater = Updater(telegram_token)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher
    # on different commands - answer in Telegram
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help))
    dp.add_handler(CommandHandler("donate", donate))
    dp.add_handler(file_cov_handler())
    dp.add_handler(feedback_cov_handler())
    dp.add_handler(CommandHandler("send", send, pass_args=True))

    # log all errors
    dp.add_error_handler(error)

    # Start the Bot
    if app_url:
        updater.start_webhook(listen="0.0.0.0",
                              port=port,
                              url_path=telegram_token)
        updater.bot.set_webhook(app_url + telegram_token)
    else:
        updater.start_polling()

    # Run the bot until the you presses Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


# Sends start message
@run_async
def start(bot, update):
    tele_id = update.message.chat.id

    if update.message.chat.type != "group":
        message = "Welcome to Cognitive Bot!\n\n"
        message += "I can provide you cognitive services. I can look for faces to look for their age, gender and " \
                   "emotions in an image. I can also do speech-to-text with an audio etc.\n\n"
        message += "Type /help to see how to use me."

        bot.sendMessage(tele_id, message)


# Sends help message
@run_async
def help(bot, update):
    tele_id = update.message.from_user.id

    message = "Simply send me an image or an audio and I will go from there with you. You can also send me links of " \
              "the image or audio.\n\n"
    message += "When sending me an image, I highly recommend you to send it as a document to prevent compression of " \
               "the image and to get a more accurate result.\n\n"
    message += "Stay tuned for updates on @cognitivebotdev"

    keyboard = [[InlineKeyboardButton("Rate me", "https://t.me/storebot?start=cognitivebot")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    bot.sendMessage(tele_id, message, reply_markup=reply_markup)


# Sends donate message
@run_async
def donate(bot, update):
    player_tele_id = update.message.from_user.id
    message = "Want to help keep me online? Please donate to %s through PayPal.\n\nDonations help " \
              "me to stay on my server and keep running." % dev_email
    bot.send_message(player_tele_id, message)


# Creates an image/audio conversation handler
def file_cov_handler():
    merged_filter = (Filters.audio | Filters.document | Filters.entity(MessageEntity.URL) | Filters.photo |
                     Filters.voice) & (~Filters.forwarded | Filters.forwarded)

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(merged_filter, check_file, pass_user_data=True)],

        states={
            WAIT_IMAGE_TASK: [RegexHandler("^[Ff]ull [Aa]nalysis", get_image_full_analysis, pass_user_data=True),
                              RegexHandler("^[Cc]ategories", get_image_category, pass_user_data=True),
                              RegexHandler("^[Cc]olou?r", get_image_colour, pass_user_data=True),
                              RegexHandler("^[Dd]escription", get_image_description, pass_user_data=True),
                              RegexHandler("^[Ff]aces", get_image_face, pass_user_data=True),
                              RegexHandler("^[Ii]mage [Tt]ype", get_image_type, pass_user_data=True),
                              RegexHandler("^[Tt]ags", get_image_tag, pass_user_data=True)],
            WAIT_AUDIO_TASK: [RegexHandler("^[Tt]o [Tt]ext", audio_to_text, pass_user_data=True)]
        },

        fallbacks=[CommandHandler("cancel", cancel), RegexHandler("^[Cc]ancel$", cancel)],

        allow_reentry=True
    )

    return conv_handler


# Checks for the document or image or audio received
def check_file(bot, update, user_data):
    file_type = None
    return_type = ConversationHandler.END

    if update.message.document:
        file_type = "doc"
    elif update.message.photo:
        file_type = "image"
    elif update.message.audio or update.message.voice:
        file_type = "audio"

    # Checks if document is an image or audio, if not, ends the conversation
    if file_type == "doc":
        doc = update.message.document
        doc_id = doc.file_id
        doc_name = doc.file_name
        doc_size = doc.file_size
        mimetype = mimetypes.guess_type(doc_name)[0]

        if mimetype.startswith("image"):
            if doc_size > cognitive_image_size_limit:
                update.message.reply_text("The file you sent is too large for me to process. Sorry.")

                return ConversationHandler.END

            user_data["image_id"] = doc_id
            return_type = WAIT_IMAGE_TASK
        elif mimetype.startswith("audio"):
            user_data["audio_id"] = doc_id
            return_type = WAIT_AUDIO_TASK
    elif file_type == "image":
        image = update.message.photo[0]
        user_data["image_id"] = image.file_id
        return_type = WAIT_IMAGE_TASK
    elif file_type == "audio":
        audio = update.message.audio if update.message.audio else update.message.voice
        user_data["audio_id"] = audio.file_id
        return_type = WAIT_AUDIO_TASK

    # Checks for received URL
    else:
        file_url = update.message.text
        mimetype = mimetypes.guess_type(file_url)[0]
        response = requests.get(file_url)

        # If URL does not give an image or audio, ends the conversation
        if mimetype.startswith("image") or mimetype.startswith("audio"):
            if response.status_code not in range(200, 209):
                update.message.reply_text("I could not retrieve the file from the URL you sent me. Please try again.")

                return ConversationHandler.END

            if mimetype.startswith("image"):
                if int(response.headers["content-length"]) > cognitive_image_size_limit:
                    update.message.reply_text("The image on the URL you sent me is too large for me to process. Sorry.")

                    return ConversationHandler.END

                user_data["image_url"] = file_url
                return_type = WAIT_IMAGE_TASK
            elif mimetype.startswith("audio"):
                user_data["audio_url"] = file_url
                return_type = WAIT_AUDIO_TASK
        else:
            return ConversationHandler.END

    user_data["msg_id"] = update.message.message_id

    if return_type == WAIT_IMAGE_TASK:
        keywords = sorted(["Categories", "Tags", "Description", "Faces", "Image Type", "Colour"])
        keywords += ["Full Analysis", "Cancel"]
        keyboard_size = 3
        keyboard = [keywords[i:i + keyboard_size] for i in range(0, len(keywords), keyboard_size)]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)

        update.message.reply_text("Please tell me what do you want me to look for on the image.",
                                  reply_markup=reply_markup)
    elif return_type == WAIT_AUDIO_TASK:
        # keywords = sorted(["To Text"])
        # keywords += ["Cancel"]
        # keyboard_size = 3
        # keyboard = [keywords[i:i + keyboard_size] for i in range(0, len(keywords), keyboard_size)]
        keyboard = [["To Text"], ["Cancel"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)

        update.message.reply_text("Please tell me what do you want me to do with the audio.",
                                  reply_markup=reply_markup)

    return return_type


# Fully analysis an image
def get_image_full_analysis(bot, update, user_data):
    if ("image_id" in user_data and not user_data["image_id"]) or \
            ("image_url" in user_data and not user_data["image_url"]):
        return

    update.message.reply_text("Analysing the image.", reply_markup=ReplyKeyboardRemove())

    tele_id = update.message.from_user.id
    msg_id = user_data["msg_id"]
    image_name = str(tele_id) + "_full"
    out_image_name = image_name + "_done"
    accent_colour = None
    face_info = {}

    headers = {"Ocp-Apim-Subscription-Key": comp_vision_token, "Content-Type": "application/octet-stream"}
    json = None
    params = {"visualFeatures": "Categories, Tags, Description, Faces, ImageType, Color",
              "details": "Celebrities, Landmarks"}
    data = convert_and_read_image(bot, update, user_data, image_name)
    result, comp_vision_err_msg = process_request("post", comp_vision_url, json, data, headers, params)

    if result:
        text = "Here is a summary of it:\n\n"
        num_categories = len(result["categories"])
        num_tags = len(result["tags"])
        target_landmark = None
        target_caption = None
        max_landmark_conf = 0
        max_caption_conf = 0

        if num_categories == 1:
            text += "Category: "
        else:
            text += "Categories: "

        # Gets categories info
        for i, category in enumerate(result["categories"]):
            name = category["name"].rstrip("_")
            name = re.sub("_", " ", name)

            if i == (num_categories - 2):
                text += name + " and "
            elif i == (num_categories - 1):
                text += name + "\n"
            else:
                text += name + ", "

            # Gets landmarks info if exists
            if "detail" in category and "landmarks" in category["detail"]:
                for landmark in category["detail"]["landmarks"]:
                    landmark_name, landmark_conf = landmark["name"], landmark["confidence"]

                    if landmark_conf > max_landmark_conf:
                        target_landmark = landmark_name
                        max_landmark_conf = landmark_conf

        if num_tags == 1:
            text += "Tag: "
        else:
            text += "Tags: "

        # Gets tags info
        for i, category in enumerate(result["tags"]):
            tag_name = "#" + category["name"].rstrip("_")
            tag_name = re.sub(" ", "", tag_name)

            if i == (num_tags - 2):
                text += tag_name + " and "
            elif i == (num_tags - 1):
                text += tag_name + "\n\n"
            else:
                text += tag_name + ", "

        # Gets description
        if target_landmark:
            text += "Description: it's the %s.\n\n" % target_landmark
        else:
            for caption in result["description"]["captions"]:
                caption_text, caption_conf = caption["text"], caption["confidence"]

                if caption_conf > max_caption_conf:
                    target_caption = caption_text
                    max_caption_conf = caption_conf

            text += "Description: it's %s.\n\n" % target_caption

        image_type = result["imageType"]
        clip_art_type, line_drawing_type = image_type["clipArtType"], image_type["lineDrawingType"]
        clip_art_type = clip_art_types[clip_art_type]
        text += "Clip art type: %s\n" % clip_art_type

        if line_drawing_type:
            text += "Line drawing: yes\n\n"
        else:
            text += "Line drawing: no\n\n"

        # Gets colour info
        colour = result["color"]
        foreground_colour = colour["dominantColorForeground"].lower()
        background_colour = colour["dominantColorBackground"].lower()
        accent_colour = "#" + colour["accentColor"]
        is_bw = colour["isBWImg"]
        dominant_colours = ", ".join(map(str.lower, colour["dominantColors"]))

        if is_bw:
            text += "Black and white image: yes\n"
        else:
            text += "Black and white image: no\n"

        text += "Foreground dominant colour: %s\n" % foreground_colour
        text += "Background dominant colour: %s\n" % background_colour
        text += "Dominant colours: %s\n" % dominant_colours
        text += "Accent colour: %s\n\n" % accent_colour
        text += "I am still analysing the faces on the image. You can look at the summary while you are waiting."

        update.message.reply_text(text, reply_to_message_id=msg_id)

        # Stores face info for emotion analysis
        for face in result["faces"]:
            age, gender = face["age"], face["gender"]
            face_rectangle = face["faceRectangle"]
            left = face_rectangle["left"]
            top = face_rectangle["top"]
            width = face_rectangle["width"]
            height = face_rectangle["height"]

            face_info[(left, top, width, height)] = (age, gender)
    elif not comp_vision_err_msg:
        update.message.reply_text("Something went wrong. Please try again.")

        if os.path.exists(image_name):
            os.remove(image_name)
        if os.path.exists(out_image_name):
            os.remove(out_image_name)

        return ConversationHandler.END

    if face_info:
        face_rectangles = []

        for face_rectangle in face_info:
            face_rectangles.append(",".join(map(str, face_rectangle)))

        params = {"faceRectangles": ";".join(face_rectangles)}

    headers = {"Ocp-Apim-Subscription-Key": emotion_token, "Content-Type": "application/octet-stream"}
    result, emotion_err_msg = process_request("post", emotion_url, json, data, headers, params)

    if result:
        process_image_face(image_name, out_image_name, result, face_info, accent_colour)

        update.message.reply_document(open(out_image_name, "rb"), caption="Here are the faces analysis on the image.")

        if comp_vision_err_msg and not emotion_err_msg:
            update.message.reply_text("I could only look at the emotions on the image but not the age and gender as I "
                                      "probably ran out of quota of processing that information.")
    elif emotion_err_msg:
        update.message.reply_text(emotion_err_msg)
    else:
        update.message.reply_text("I could not find any faces on the image.")

    if os.path.exists(image_name):
        os.remove(image_name)
    if os.path.exists(out_image_name):
        os.remove(out_image_name)

    return ConversationHandler.END


# Annotates the faces on the image
def process_image_face(image_name, out_image_name, result, face_info, accent_colour):
    im = Image.open(image_name).convert("RGB")
    draw = ImageDraw.Draw(im, "RGBA")
    font = ImageFont.truetype("segoeuil.ttf", 16)

    for face in result:
        face_rectangle = face["faceRectangle"]
        left = face_rectangle["left"]
        top = face_rectangle["top"]
        width = face_rectangle["width"]
        height = face_rectangle["height"]
        right = left + width
        bottom = top + height
        top_offset = top - 50 if top - 50 >= 0 else 0
        text = ""

        if (left, top, width, height) in face_info:
            age, gender = face_info[(left, top, width, height)]
            text += "%s %d\n" % (gender, age)

        text += max(face["scores"].items(), key=operator.itemgetter(1))[0].capitalize()
        text_size = draw.multiline_textsize(text, font)

        draw.rectangle([left, top, right, bottom])
        draw.rectangle([left, top_offset, left + text_size[0], top_offset + text_size[1]],
                       fill=(241, 241, 242, 170))

        if accent_colour:
            draw.multiline_text((left, top_offset), text, accent_colour, font)
        else:
            draw.multiline_text((left, top_offset), text, (25, 149, 173), font)

    im.save(out_image_name, "JPEG")


# Gets categories of the image
def get_image_category(bot, update, user_data):
    if ("image_id" in user_data and not user_data["image_id"]) or \
            ("image_url" in user_data and not user_data["image_url"]):
        return

    update.message.reply_text("Looking for the categories on the image.", reply_markup=ReplyKeyboardRemove())

    tele_id = update.message.from_user.id
    msg_id = user_data["msg_id"]
    image_name = str(tele_id) + "_category"

    headers = {"Ocp-Apim-Subscription-Key": comp_vision_token, "Content-Type": "application/octet-stream"}
    json = None
    params = {"visualFeatures": "Categories"}
    data = convert_and_read_image(bot, update, user_data, image_name)
    result, err_msg = process_request("post", comp_vision_url, json, data, headers, params)

    if result:
        num_categories = len(result["categories"])

        if num_categories == 1:
            text = "I think it belongs to the category of "
        else:
            text = "I think it belongs to the categories of "

        for i, category in enumerate(result["categories"]):
            name = category["name"].rstrip("_")
            name = re.sub("_", " ", name)

            if i == (num_categories - 2):
                text += name + " and "
            elif i == (num_categories - 1):
                text += name
            else:
                text += name + ", "

        update.message.reply_text(text, reply_to_message_id=msg_id)
    elif err_msg:
        update.message.reply_text(err_msg)

    if os.path.exists(image_name):
        os.remove(image_name)

    return ConversationHandler.END


# Gets colour info of the image
def get_image_colour(bot, update, user_data):
    if ("image_id" in user_data and not user_data["image_id"]) or \
            ("image_url" in user_data and not user_data["image_url"]):
        return

    update.message.reply_text("Analysing the colours on the image.", reply_markup=ReplyKeyboardRemove())

    tele_id = update.message.from_user.id
    msg_id = user_data["msg_id"]
    image_name = str(tele_id) + "_colour"

    headers = {"Ocp-Apim-Subscription-Key": comp_vision_token, "Content-Type": "application/octet-stream"}
    json = None
    params = {"visualFeatures": "Color"}
    data = convert_and_read_image(bot, update, user_data, image_name)
    result, err_msg = process_request("post", comp_vision_url, json, data, headers, params)

    if result:
        colour = result["color"]
        foreground_colour = colour["dominantColorForeground"]
        background_colour = colour["dominantColorBackground"].lower()
        accent_colour = colour["accentColor"]
        is_bw = colour["isBWImg"]
        dominant_colours = ", ".join(map(str.lower, colour["dominantColors"]))

        if is_bw:
            text = "This is a black and white image.\n\n"
        else:
            text = "This is not a black and white image.\n\n"

        text += "%s and %s dominate the foreground and background respectively. " % \
                (foreground_colour, background_colour)
        text += "The dominant colours include %s.\n\n" % dominant_colours
        text += "And the accent colour is #%s." % accent_colour

        update.message.reply_text(text, reply_to_message_id=msg_id)
    elif err_msg:
        update.message.reply_text(err_msg)

    if os.path.exists(image_name):
        os.remove(image_name)

    return ConversationHandler.END


# Gets a description of the image
def get_image_description(bot, update, user_data):
    if ("image_id" in user_data and not user_data["image_id"]) or \
            ("image_url" in user_data and not user_data["image_url"]):
        return

    update.message.reply_text("Trying to describe the image.", reply_markup=ReplyKeyboardRemove())

    tele_id = update.message.from_user.id
    msg_id = user_data["msg_id"]
    image_name = str(tele_id) + "_description"

    headers = {"Ocp-Apim-Subscription-Key": comp_vision_token, "Content-Type": "application/octet-stream"}
    json = None
    params = {"visualFeatures": "Description", "details": "Landmarks"}
    data = convert_and_read_image(bot, update, user_data, image_name)
    result, err_msg = process_request("post", comp_vision_url, json, data, headers, params)

    if result:
        target_landmark = None
        target_caption = None
        max_landmark_conf = 0
        max_caption_conf = 0

        if "categories" in result:
            for category in result["categories"]:
                if "detail" in category and "landmarks" in category["detail"]:
                    for landmark in category["detail"]["landmarks"]:
                        landmark_name, landmark_conf = landmark["name"], landmark["confidence"]

                        if landmark_conf > max_landmark_conf:
                            target_landmark = landmark_name
                            max_landmark_conf = landmark_conf

        if target_landmark:
            text = "I'll say it's the %s." % target_landmark
        else:
            for caption in result["description"]["captions"]:
                caption_text, caption_conf = caption["text"], caption["confidence"]

                if caption_conf > max_caption_conf:
                    target_caption = caption_text
                    max_caption_conf = caption_conf

            text = "I'll say it's %s." % target_caption

        update.message.reply_text(text, reply_to_message_id=msg_id)
    elif err_msg:
        update.message.reply_text(err_msg)

    if os.path.exists(image_name):
        os.remove(image_name)

    return ConversationHandler.END


# Gets faces (age, emotion, gender) on the image, and adds annotation onto the image
def get_image_face(bot, update, user_data):
    if ("image_id" in user_data and not user_data["image_id"]) or \
            ("image_url" in user_data and not user_data["image_url"]):
        return

    update.message.reply_text("Analysing the faces on the image.", reply_markup=ReplyKeyboardRemove())

    tele_id = update.message.from_user.id
    image_name = str(tele_id) + "_face"
    out_image_name = image_name + "_done"
    accent_colour = None
    face_info = {}

    headers = {"Ocp-Apim-Subscription-Key": comp_vision_token, "Content-Type": "application/octet-stream"}
    json = None
    params = {"visualFeatures": "Faces, Color"}
    data = convert_and_read_image(bot, update, user_data, image_name)
    result, face_err_msg = process_request("post", comp_vision_url, json, data, headers, params)

    if result:
        accent_colour = "#" + result["color"]["accentColor"]

        for face in result["faces"]:
            age, gender = face["age"], face["gender"]
            face_rectangle = face["faceRectangle"]
            left = face_rectangle["left"]
            top = face_rectangle["top"]
            width = face_rectangle["width"]
            height = face_rectangle["height"]

            face_info[(left, top, width, height)] = (age, gender)
    elif not face_err_msg:
        update.message.reply_text("I could not find any faces on the image.")

        if os.path.exists(image_name):
            os.remove(image_name)
        if os.path.exists(out_image_name):
            os.remove(out_image_name)

        return ConversationHandler.END

    if face_info:
        face_rectangles = []

        for face_rectangle in face_info:
            face_rectangles.append(",".join(map(str, face_rectangle)))

        params = {"faceRectangles": ";".join(face_rectangles)}

    headers = {"Ocp-Apim-Subscription-Key": emotion_token, "Content-Type": "application/octet-stream"}
    result, emotion_err_msg = process_request("post", emotion_url, json, data, headers, params)

    if result:
        process_image_face(image_name, out_image_name, result, face_info, accent_colour)

        update.message.reply_document(open(out_image_name, "rb"), caption="Here are the faces on the image.")
    elif face_err_msg and not emotion_err_msg:
        update.message.reply_text("I could only look at the emotions on the image but not the age and gender as I "
                                  "probably ran out of quota of processing that information.")
    elif emotion_err_msg:
        update.message.reply_text(emotion_err_msg)
    else:
        update.message.reply_text("I could not find any faces on the image.")

    if os.path.exists(image_name):
        os.remove(image_name)
    if os.path.exists(out_image_name):
        os.remove(out_image_name)

    return ConversationHandler.END


# Gets tags of the image
def get_image_tag(bot, update, user_data):
    if ("image_id" in user_data and not user_data["image_id"]) or \
            ("image_url" in user_data and not user_data["image_url"]):
        return

    update.message.reply_text("Looking for the tags on the image.", reply_markup=ReplyKeyboardRemove())

    tele_id = update.message.from_user.id
    msg_id = user_data["msg_id"]
    image_name = str(tele_id) + "_tag"

    headers = {"Ocp-Apim-Subscription-Key": comp_vision_token, "Content-Type": "application/octet-stream"}
    json = None
    params = {"visualFeatures": "Tags"}
    data = convert_and_read_image(bot, update, user_data, image_name)
    result, err_msg = process_request("post", comp_vision_url, json, data, headers, params)

    if result:
        num_tags = len(result["tags"])

        if num_tags == 1:
            text = "I think it is "
        else:
            text = "I think it has tags of "

        for i, category in enumerate(result["tags"]):
            tag_name = "#" + category["name"].rstrip("_")
            tag_name = re.sub(" ", "", tag_name)

            if i == (num_tags - 2):
                text += tag_name + " and "
            elif i == (num_tags - 1):
                text += tag_name
            else:
                text += tag_name + ", "

        update.message.reply_text(text, reply_to_message_id=msg_id)
    elif err_msg:
        update.message.reply_text(err_msg)

    if os.path.exists(image_name):
        os.remove(image_name)

    return ConversationHandler.END


# Gets image type
def get_image_type(bot, update, user_data):
    if ("image_id" in user_data and not user_data["image_id"]) or \
            ("image_url" in user_data and not user_data["image_url"]):
        return

    update.message.reply_text("Identifying the image type.", reply_markup=ReplyKeyboardRemove())

    tele_id = update.message.from_user.id
    msg_id = user_data["msg_id"]
    image_name = str(tele_id) + "_type"

    headers = {"Ocp-Apim-Subscription-Key": comp_vision_token, "Content-Type": "application/octet-stream"}
    json = None
    params = {"visualFeatures": "ImageType"}
    data = convert_and_read_image(bot, update, user_data, image_name)
    result, err_msg = process_request("post", comp_vision_url, json, data, headers, params)

    if result:
        image_type = result["imageType"]
        clip_art_type, line_drawing_type = image_type["clipArtType"], image_type["lineDrawingType"]
        clip_art_type = clip_art_types[clip_art_type]

        if clip_art_type == "ambiguous":
            text = "I'm not sure if it's a clip art or not, but "
        else:
            text = "I think it's a %s, and " % clip_art_type

        if line_drawing_type:
            text += "I think it's a line drawing."
        else:
            text += "I think it's not a line drawing."

        update.message.reply_text(text, reply_to_message_id=msg_id)
    elif err_msg:
        update.message.reply_text(err_msg)

    if os.path.exists(image_name):
        os.remove(image_name)

    return ConversationHandler.END


# Checks if the image format is supported, if not converts it into JPEG format
def convert_and_read_image(bot, update, user_data, image_name):
    if "image_id" in user_data and user_data["image_id"]:
        image_id = user_data["image_id"]
        del user_data["image_id"]
        image_file = bot.get_file(image_id)
        image_file.download(image_name)
    else:
        image_url = user_data["image_url"]
        del user_data["image_url"]
        response = requests.get(image_url)

        if response.status_code == 200:
            with open(image_name, "wb") as f:
                for chunk in response:
                    f.write(chunk)
        else:
            update.message.reply_text("I could not download the image from the URL you sent me. Please check the URL "
                                      "and try again.")

    im = Image.open(image_name)
    if im.format not in ("JPEG", "PNG", "GIF", "BMP"):
        im.save(image_name, "JPEG")

    with open(image_name, "rb") as f:
        data = f.read()

    return data


def audio_to_text(bot, update, user_data):
    if ("audio_id" in user_data and not user_data["audio_id"]) or \
            ("audio_url" in user_data and not user_data["audio_url"]):
        return

    update.message.reply_text("Analysing your audio.", reply_markup=ReplyKeyboardRemove())

    tele_id = update.message.from_user.id
    msg_id = user_data["msg_id"]
    audio_name = str(tele_id) + "_audio.wav"
    audio_temp_name = audio_name + "_temp"

    audio = convert_and_read_audio(bot, update, user_data, audio_name, audio_temp_name)
    r = sr.Recognizer()

    try:
        text = r.recognize_bing(audio, key=bing_speech_token) + "\n"
    except sr.UnknownValueError:
        text = "I could not understand the audio. Sorry"
    except sr.RequestError as e:
        logger.error("Could not request results from Microsoft Bing Voice Recognition service; {0}".format(e))
        text = "Something went wrong. Please try again."

    update.message.reply_text(text, reply_to_message_id=msg_id)

    if os.path.exists(audio_name):
        os.remove(audio_name)
    if os.path.exists(audio_temp_name):
        os.remove(audio_temp_name)

    return ConversationHandler.END


def convert_and_read_audio(bot, update, user_data, audio_name, audio_temp_name):
    if "audio_id" in user_data and user_data["audio_id"]:
        audio_id = user_data["audio_id"]
        del user_data["audio_id"]
        audio_file = bot.get_file(audio_id)
        audio_file.download(audio_temp_name)
    else:
        audio_url = user_data["audio_url"]
        del user_data["audio_url"]
        response = requests.get(audio_url)

        if response.status_code == 200:
            with open(audio_temp_name, "wb") as f:
                for chunk in response:
                    f.write(chunk)
        else:
            update.message.reply_text("I could not download the audio from the URL you sent me. Please check the URL "
                                      "and try again.")

    command = "ffmpeg -y -i {input_audio} {output_audio}". \
        format(input_audio=audio_temp_name, output_audio=audio_name)

    process = Popen(shlex.split(command), stdout=PIPE, stderr=PIPE)
    process_out, process_err = process.communicate()

    if process.returncode != 0 or not os.path.exists(audio_name) or "[Errno" in process_err.decode("utf8").strip():
        update.message.reply_text("Something went wrong")

        return ConversationHandler.END

    r = sr.Recognizer()
    with sr.AudioFile(audio_name) as source:
        audio = r.record(source)

    return audio


# Processes request
def process_request(method, url, json, data, headers, params):
    result = None
    err_msg = None
    retry_count = 0
    max_retry_num = 3

    while True:
        response = requests.request(method=method, url=url, json=json, data=data, headers=headers, params=params)

        if response.status_code == 403:
            err_msg = "I ran out of quota for processing images. Please try again later. Sorry."
        elif response.status_code == 429:
            if retry_count <= max_retry_num:
                time.sleep(1)
                retry_count += 1

                continue
            else:
                err_msg = "Something went wrong. Please try again."

                break
        elif response.status_code == 200:
            if int(response.headers["content-length"]) != 0 and \
                            "application/json" in response.headers["content-type"].lower():
                result = response.json() if response.content else None
        else:
            err_msg = "Something went wrong. Please try again."

            try:
                logger.error("Error code: %d, Message: %s" % (response.status_code, response.json()["message"]))
            except KeyError:
                pass

            try:
                logger.error("Error code: %d, Message: %s" %
                             (response.status_code, response.json()["error"]["message"]))
            except KeyError:
                pass

        break

    return result, err_msg


# Creates a feedback conversation handler
def feedback_cov_handler():
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("feedback", feedback)],

        states={
            0: [MessageHandler(Filters.text, receive_feedback)],
        },

        fallbacks=[CommandHandler("cancel", cancel)],

        allow_reentry=True
    )

    return conv_handler


# Sends a feedback message
@run_async
def feedback(bot, update):
    update.message.reply_text("Please send me your feedback or type /cancel to cancel this operation. My developer "
                              "can understand English and Chinese.")

    return 0


# Saves a feedback
@run_async
def receive_feedback(bot, update):
    feedback_msg = update.message.text
    valid_lang = False
    langdetect.DetectorFactory.seed = 0
    langs = langdetect.detect_langs(feedback_msg)

    for lang in langs:
        if lang.lang in ("en", "zh-tw", "zh-cn"):
            valid_lang = True
            break

    if not valid_lang:
        update.message.reply_text("The feedback you sent is not in English or Chinese. Please try again.")
        return 0

    update.message.reply_text("Thank you for your feedback, I will let my developer know.")

    if is_email_feedback:
        server = smtplib.SMTP(smtp_host)
        server.ehlo()
        server.starttls()
        server.login(dev_email, dev_email_pw)

        text = "Feedback received from %d\n\n%s" % (update.message.from_user.id, update.message.text)
        message = "Subject: %s\n\n%s" % ("Telegram Big Two Bot Feedback", text)
        server.sendmail(dev_email, dev_email, message)
    else:
        logger.info("Feedback received from %d: %s" % (update.message.from_user.id, update.message.text))

    return ConversationHandler.END


# Cancels feedback opteration
@run_async
def cancel(bot, update):
    update.message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# Sends a message to a specified user
def send(bot, update, args):
    if update.message.from_user.id == dev_tele_id:
        tele_id = int(args[0])
        message = " ".join(args[1:])

        try:
            bot.send_message(tele_id, message)
        except Exception as e:
            logger.exception(e)
            bot.send_message(dev_tele_id, "Failed to send message")


def error(bot, update, error_msg):
    logger.warning("Update '%s' caused error '%s'" % (update, error_msg))


if __name__ == "__main__":
    main()
