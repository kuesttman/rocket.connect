import dateutil.parser
import requests
from django.template import Context, Template
from django.utils import timezone
from instance.models import Server

from config import celery_app


@celery_app.task(
    retry_kwargs={"max_retries": 7, "countdown": 5},
    autoretry_for=(requests.ConnectionError,),
)
def intake_unread_messages(connector_id):
    """Reintake the Unread Messages of a given Connector"""
    from instance.models import Connector

    connector = Connector.objects.get(id=connector_id)
    Connector = connector.get_connector_class()
    c = Connector(connector, {}, type="incoming")
    unread = c.intake_unread_messages()
    return unread


@celery_app.task(
    retry_kwargs={"max_retries": 7, "countdown": 5},
    autoretry_for=(requests.ConnectionError,),
)
def alert_last_message_open_chat(
    server_token, seconds_last_message, notification_target, notification_template
):
    """alert open messages"""

    # get server
    server = Server.objects.get(external_token=server_token)
    # get rocket
    rocket = server.get_rocket_client()
    # list all open messages
    open_rooms = server.get_open_rooms()
    # process
    alerted_rooms = []
    now = timezone.now()
    # parse datetime strings to python objects

    for room in open_rooms.get("rooms", []):
        rendered_targets = []
        if room.get("lastMessage"):
            last_message = room["lastMessage"]
            ts = dateutil.parser.parse(last_message["ts"])
            delta = now - ts
            if delta.total_seconds() >= seconds_last_message:
                alerted_rooms.append(room["_id"])
                # adjust context dict
                room["id"] = room["_id"]
                room["lm_obj"] = dateutil.parser.parse(room["lm"])
                room["ts_obj"] = dateutil.parser.parse(room["ts"])
                # render notification_template
                context_dict = {
                    "room": room,
                    # "open_rooms": open_rooms,
                    "external_url": server.get_external_url(),
                }
                context = Context(context_dict)
                template = Template(notification_template)
                for target in notification_target.split(","):

                    # render message
                    message = template.render(context)
                    if target.startswith("#"):
                        rendered_targets.append(target)
                        sent = rocket.chat_post_message(
                            text=message, channel=target.replace("#", "")
                        )
                    else:

                        # target may contain variables
                        target_template = Template(target)
                        rendered_target = target_template.render(context)
                        rendered_targets.append(rendered_target)
                        dm = rocket.im_create(username=rendered_target)
                        if dm.ok:
                            room_id = dm.json()["room"]["rid"]
                            sent = rocket.chat_post_message(
                                text=message, room_id=room_id
                            )
                            print("SENT! ", sent)

    # return findings
    return {
        "alerted_rooms": alerted_rooms,
        "now": str(now),
        "seconds_last_message": seconds_last_message,
        "notification_target_unrendered": notification_target,
        "rendered_targets": rendered_targets,
    }


@celery_app.task(
    retry_kwargs={"max_retries": 7, "countdown": 5},
    autoretry_for=(requests.ConnectionError,),
)
def server_maintenance(server_token):
    """do all sorts of server maintenance"""
    server = Server.objects.get(external_token=server_token)
    response = {}
    # sync room
    response["room_sync"] = server.room_sync(execute=True)
    # return results
    return response


@celery_app.task(
    retry_kwargs={"max_retries": 7, "countdown": 5},
    autoretry_for=(requests.ConnectionError,),
)
def alert_open_rooms_generic_webhook(server_token, endpoint):
    """send a payload to a configured endpoint"""

    # get server
    server = Server.objects.get(external_token=server_token)
    # list all open messages
    open_rooms = server.get_open_rooms()
    # enhance payloads
    open_rooms["external_url"] = server.get_external_url()
    # process
    response = requests.post(endpoint, json=open_rooms)
    return response.ok
