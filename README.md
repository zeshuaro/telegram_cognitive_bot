# Telegram Cognitive Bot

Telegram Bot that provides cognitive services

Connect to [Bot](https://t.me/cognitivebot)

Stay tuned for updates and new releases on the [Telegram Channel](https://t.me/cognitivebotdev)

Find the bot at [Store Bot](https://storebot.me/bot/cognitivebot)

## Getting Started

These instructions will get you a copy of the project up and running on your local machine for development and 
testing purposes

### Prerequisites

Run the following command to install the required libraries:

```
pip install -r requirements.txt
```

Below is a list of the main libraries that are included:

* [Python Telegram Bot](https://github.com/python-telegram-bot/python-telegram-bot)
* [Pillow](https://github.com/python-pillow/Pillow)
* [SpeechRecognition](https://github.com/Uberi/speech_recognition)

The bot uses [Cognitive Services APIs](https://azure.microsoft.com/en-au/services/cognitive-services/) provided my 
Microsoft Azure.

Make a `.env` file and put your telegram token in there. 

You will also need to include the tokens and URLs of the Computer Vision API, Emotion API and Bing Speech API.

If you want to use the webhook method to run the bot, also include `APP_URL` and `PORT` in the `.env` file. If you 
want to use polling instead, do not include `APP_URL` in your `.env` file.

Below is an example:

```
TELEGRAM_TOKEN=<telegram_token>
COMP_VISION_TOKEN=<comp_vision_token>
COMP_VISION_ANALYSIS_URL=<comp_vision_analysis_url>
COMP_VISION_TEXT_URL=<comp_vision_text_url>
EMOTION_TOKEN=<emotion_token>
EMOTION_URL=<emotion_url>
BING_SPEECH_TOKEN=<bing_speech_token>
```