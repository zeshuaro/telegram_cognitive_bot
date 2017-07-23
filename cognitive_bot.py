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
import smtplib
import time

from PIL import Image, ImageDraw, ImageFont

from telegram import MessageEntity, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Updater, CommandHandler, ConversationHandler, MessageHandler, Filters, RegexHandler
from telegram.ext.dispatcher import run_async

from cov_states import *

# Enable logging
logging.basicConfig(format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %I:%M:%S %p",
                    level=logging.INFO)
logger = logging.getLogger(__name__)

dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
dotenv.load(dotenv_path)
app_url = os.environ.get("APP_URL")
port = int(os.environ.get("PORT", "5000"))

telegram_token = os.environ.get("TELEGRAM_TOKEN_BETA")
dev_tele_id = int(os.environ.get("DEV_TELE_ID"))
dev_email = os.environ.get("DEV_EMAIL") if os.environ.get("DEV_EMAIL") else "sample@email.com"
dev_email_pw = os.environ.get("DEV_EMAIL_PW")
is_email_feedback = os.environ.get("IS_EMAIL_FEEDBACK")
smtp_host = os.environ.get("SMTP_HOST")

comp_vision_token = os.environ.get("COMP_VISION_TOKEN")
comp_vision_url = os.environ.get("COMP_VISION_URL")
emotion_token = os.environ.get("EMOTION_TOKEN")
emotion_url = os.environ.get("EMOTION_URL")

cognitive_image_size_limit = 4000000
download_size_limit = 20000000
upload_size_limit = 50000000


# Sends start message
@run_async
def start(bot, update):
    tele_id = update.message.chat.id

    if update.message.chat.type != "group":
        message = "Start"

        bot.sendMessage(tele_id, message)


# Sends help message
@run_async
def help(bot, update):
    player_tele_id = update.message.from_user.id

    message = "Help"

    bot.sendMessage(player_tele_id, message)


# Sends donate message
@run_async
def donate(bot, update):
    player_tele_id = update.message.from_user.id
    message = "Want to help keep me online? Please donate to %s through PayPal.\n\nDonations help " \
              "me to stay on my server and keep running." % dev_email
    bot.send_message(player_tele_id, message)


def image_cov_handler():
    merged_filter = (Filters.document | Filters.entity(MessageEntity.URL) | Filters.photo) & \
             (~Filters.forwarded | Filters.forwarded)

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(merged_filter, check_image, pass_user_data=True)],

        states={
            RECEIVE_OPTION: [RegexHandler("^[Cc]ategories", get_image_category, pass_user_data=True),
                             RegexHandler("^[Dd]escription", get_image_description, pass_user_data=True),
                             RegexHandler("^[Ff]aces", get_image_face, pass_user_data=True),
                             RegexHandler("^[Tt]ags", get_image_tag, pass_user_data=True)],
        },

        fallbacks=[CommandHandler("cancel", cancel)],

        allow_reentry=True
    )

    return conv_handler


# Validates image received
def check_image(bot, update, user_data):
    if update.message.document or update.message.photo:
        is_doc = update.message.document
        image = update.message.document if is_doc else update.message.photo[0]
        image_id = image.file_id
        image_name = image.file_name if is_doc else None
        image_size = image.file_size

        if is_doc:
            mimetype = mimetypes.guess_type(image_name)[0]

            if not mimetype.startswith("image"):
                update.message.reply_text("The file you sent is not an image. Please try again.")

                return ConversationHandler.END

        if image_size > cognitive_image_size_limit:
            update.message.reply_text("The file you sent is too large for me to process. Sorry.")

            return ConversationHandler.END

        user_data["image_id"] = image_id
    else:
        image_url = update.message.text
        mimetype = mimetypes.guess_type(image_url)[0]
        response = requests.get(image_url)

        if not mimetype.startswith("image"):
            update.message.reply_text("The URL you sent is not an image. Please try again.")

            return ConversationHandler.END
        elif response.status_code not in range(200, 209):
            update.message.reply_text("I could not retrieve the image from the URL you sent me. Please try again.")

            return ConversationHandler.END
        elif int(response.headers["content-length"]) > cognitive_image_size_limit:
            update.message.reply_text("The image on the URL you sent me is too large for me to process. Sorry.")

            return ConversationHandler.END

        user_data["image_url"] = image_url

    user_data["msg_id"] = update.message.message_id

    keywords = sorted(["Categories", "Tags", "Description", "Faces", "Image Type", "Colour"])
    keywords.insert(0, "Full Analysis")
    keyboard_size = 3
    keyboard = [keywords[i:i + keyboard_size] for i in range(0, len(keywords), keyboard_size)]
    reply_markup = ReplyKeyboardMarkup(keyboard)

    update.message.reply_text("Please tell me what do you want me to look for on the image.",
                              reply_markup=reply_markup,
                              one_time_keyboard=True)

    return RECEIVE_OPTION


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
    data = fix_and_read_image(bot, update, user_data, image_name)
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
    data = fix_and_read_image(bot, update, user_data, image_name)
    result, err_msg = process_request("post", comp_vision_url, json, data, headers, params)

    if result:
        target_landmark = None
        target_caption = None
        max_landmark_conf = 0
        max_caption_conf = 0

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
                print(caption)
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


# Gets emotions on the image, and adds annotation onto the image
def get_image_face(bot, update, user_data):
    if ("image_id" in user_data and not user_data["image_id"]) or \
            ("image_url" in user_data and not user_data["image_url"]):
        return

    update.message.reply_text("Analysing the faces on the image.", reply_markup=ReplyKeyboardRemove())

    tele_id = update.message.from_user.id
    image_name = str(tele_id) + "_face"
    out_image_name = image_name + "_done"
    face_info = {}

    headers = {"Ocp-Apim-Subscription-Key": comp_vision_token, "Content-Type": "application/octet-stream"}
    json = None
    params = {"visualFeatures": "Faces"}
    data = fix_and_read_image(bot, update, user_data, image_name)
    result, face_err_msg = process_request("post", comp_vision_url, json, data, headers, params)

    if result:
        for face in result["faces"]:
            age, gender = face["age"], face["gender"]
            face_rectangle = face["faceRectangle"]
            left = face_rectangle["left"]
            top = face_rectangle["top"]
            width = face_rectangle["width"]
            height = face_rectangle["height"]

            face_info[(left, top, width, height)] = (age, gender)
    elif not face_err_msg:
        update.message.reply_text("I could not find any faces on the image. Please send me another image with faces.")

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
        im = Image.open(image_name).convert("RGB")
        draw = ImageDraw.Draw(im, "RGBA")
        font = ImageFont.truetype("segoeuil.ttf", 30)

        for face in result:
            face_rectangle = face["faceRectangle"]
            left = face_rectangle["left"]
            top = face_rectangle["top"]
            width = face_rectangle["width"]
            height = face_rectangle["height"]
            right = left + width
            bottom = top + height
            top_offset = top - 80 if top - 80 >= 0 else 0
            text = ""

            if (left, top, width, height) in face_info:
                age, gender = face_info[(left, top, width, height)]
                text += "%s %d\n" % (gender, age)

            text += max(face["scores"].items(), key=operator.itemgetter(1))[0].capitalize()
            text_size = draw.multiline_textsize(text, font)

            draw.rectangle([left, top, right, bottom])
            draw.rectangle([left, top_offset, left + text_size[0], top_offset + text_size[1]],
                           fill=(241, 241, 242, 170))
            draw.multiline_text((left, top_offset), text, (25, 149, 173), font)

        im.show()
        im.save(out_image_name, "JPEG")

        update.message.reply_document(open(out_image_name, "rb"), caption="Here are the faces on the image.")
    elif face_err_msg and not emotion_err_msg:
        update.message.reply_text("I could only look at the emotions on the image but not the age and gender as I "
                                  "probably ran out of quota of processing that information.")
    elif emotion_err_msg:
        update.message.reply_text(emotion_err_msg)
    else:
        update.message.reply_text("I could not find any faces on the image. Please send me another image with faces.")

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
    data = fix_and_read_image(bot, update, user_data, image_name)

    result, err_msg = process_request("post", comp_vision_url, json, data, headers, params)

    if result:
        num_tags = len(result["tags"])

        if num_tags == 1:
            text = "I think it is "
        else:
            text = "I think it has tags of "

        for i, category in enumerate(result["tags"]):
            tag_name = "#" + category["name"].rstrip("_")
            tag_name = re.sub("_", " ", tag_name)

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


# Checks if the image format is supported, if not transform it into JPEG format
def fix_and_read_image(bot, update, user_data, image_name):
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


def error(bot, update, error):
    logger.warning("Update '%s' caused error '%s'" % (update, error))


def main():
    # Create the EventHandler and pass it your bot"s token.
    updater = Updater(telegram_token)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher
    # on different commands - answer in Telegram
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help))
    dp.add_handler(CommandHandler("donate", donate))
    dp.add_handler(image_cov_handler())
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


if __name__ == "__main__":
    main()
