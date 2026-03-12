# app.utils.notify_depends.py
from fastapi import WebSocket
from typing import Dict
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.auth_models import NotificationStorage, CandidateNotificationStorage
from app.schemas.user_schemas import NotificationStructure

employee_connections: Dict[str, WebSocket] = {}
candidate_connections: Dict[str, WebSocket] = {}


async def send_notification_to_employee(employee_id:str, notification_data:NotificationStructure, db:Session):
    try:
        key = f"{employee_id}"
        websocket = employee_connections.get(key)
 
        ## store notification at notification storage
        notification_row = NotificationStorage(employee_id = employee_id, data = notification_data.model_dump())
        db.add(notification_row)
        db.commit()
        db.refresh(notification_row)
 
        if websocket:
            await websocket.send_json({"notification_id":notification_row.notification_id, "data":notification_row.data})
    except Exception as e:
        print(f"Error while sending request to {key} : {e}")
 


async def send_notification_to_candidate(candidate_id: str, notification_data: NotificationStructure, db: Session = None):
    try:
        key = f"{candidate_id}"
        websocket = candidate_connections.get(key)

        ## 
        notification_row = CandidateNotificationStorage(candidate_id = candidate_id, data = notification_data.model_dump())
        db.add(notification_row)
        db.commit()
        db.refresh(notification_row)
 
        if websocket:
            await websocket.send_json({"notification_id":notification_row.notification_id, "data":notification_row.data})
    except Exception as e:
        print(f"Error while sending request to {key} : {e}")

