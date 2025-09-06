import logging
from fastapi import HTTPException
import json
import re
import requests
import os

logger = logging.getLogger(__name__)


def is_status_update(body):
    """
    Check if the incoming webhook event is a WhatsApp status update.
    """
    return (
        body.get("object")
        and body.get("entry")
        and body["entry"][0].get("changes")
        and body["entry"][0]["changes"][0].get("value")
        and body["entry"][0]["changes"][0]["value"].get("statuses")
    )


def parse_status_update(body):
    """
    Parse the WhatsApp status update from the webhook event.
    """
    try:
        status = body["entry"][0]["changes"][0]["value"]["statuses"][0]
        logger.debug(f"WhatsApp Status - {status}")
        return status
    
    except Exception as e:
        logger.error(f"Error parsing status update: {e}")
        raise HTTPException(status_code=400, detail="Invalid WhatsApp status update structure")
    

def is_valid_message(body):
    """
    Check if the incoming webhook event is a valid WhatsApp message.
    """
    return (
        body.get("object")
        and body.get("entry")
        and body["entry"][0].get("changes")
        and body["entry"][0]["changes"][0].get("value")
        and body["entry"][0]["changes"][0]["value"].get("messages")
        and body["entry"][0]["changes"][0]["value"]["messages"][0]
    )


def parse_phone_and_message(body):
    """
    Parse the phone number and message body from the WhatsApp webhook event.
    """
    try:
        obj = body["entry"][0]["changes"][0]["value"]["messages"][0]

        phone_number = obj["from"]  # extract the phone number of the sender
        msg_type = obj["type"]  # extract the type of message
        if msg_type != "text":
            raise HTTPException(status_code=400, detail="Unsupported message type")
        
        message_body = obj["text"]["body"]  # extract the text message body
        return phone_number, message_body
    
    except Exception as e:
        logger.error(f"Error parsing phone number and message: {e}")
        raise HTTPException(status_code=400, detail="Invalid WhatsApp message structure")


def send_message(phone_number, message):
    message = format_message_content(message)
    data = json.dumps({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone_number,
            "type": "text",
            "text": {"preview_url": False, "body": message},
        })

    headers = {
        "Content-type": "application/json",
        "Authorization": f"Bearer {os.environ.get('META_ACCESS_TOKEN')}",
    }

    api_version = os.environ.get("GRAPH_API_VERSION")
    phone_id = os.environ.get("META_PHONE_NUMBER_ID")
    url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"

    try:
        response = requests.post(url, data=data, headers=headers)  
        response.raise_for_status()
    except requests.Timeout:
        logger.error("Whatsapp send message request timed out")
        raise HTTPException(status_code=408, detail="Request Timeout")
    except requests.RequestException as e:
        logger.error(f"Internal Server Error, failed to send message : {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error, failed to send message")
    else:
        logger.debug(f"send_message - status: {response.status_code}")
        logger.info(f"Outgoing message to {phone_number}: {message}")

        return response


def format_message_content(text: str) -> str:
    """
    Cleans and converts input text into WhatsApp-compatible formatting.
    - Removes 【...】 blocks
    - Converts Markdown styles to WhatsApp equivalents
    """

    # 1. Remove brackets and their content 【...】
    text = re.sub(r"\【.*?\】", "", text).strip()

    # 2. Convert Markdown bold (**text**) → WhatsApp bold (*text*)
    text = re.sub(r"\*\*(.*?)\*\*", r"*\1*", text)

    # 3. Convert Markdown italics (_text_) → WhatsApp italics (_text_)
    # (ensure it doesn't conflict with bold/underscore usage)
    text = re.sub(r"_(.*?)_", r"_\1_", text)

    # 4. Convert Markdown strikethrough (~~text~~) → WhatsApp (~text~)
    text = re.sub(r"~~(.*?)~~", r"~\1~", text)

    # 5. Convert Markdown inline code (`text`) → WhatsApp monospace (`text`)
    text = re.sub(r"`(.*?)`", r"`\1`", text)

    return text
