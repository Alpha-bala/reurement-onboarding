# app.routers.notifications.py
from fastapi import WebSocket, WebSocketDisconnect, APIRouter, Depends, HTTPException, status
from app.database import get_db, db_session
from sqlalchemy.orm import Session
from app.models.auth_models import ProfileInformation
from app.models.user_models import CandidateProfileInformation
from app.utils.notify_depends import candidate_connections, employee_connections
from app.models.auth_models import NotificationStorage, CandidateNotificationStorage

router = APIRouter(prefix="/notification", tags=["push_notification"])


@router.websocket("/ws/employee/{employee_id}")
async def websocket_endpoint(
    employee_id:str,
    websocket: WebSocket,
    ):
 
    try:  
 
        ## before creating websocket for the person, you need to validate him with company database
        with db_session() as db:
            if not db.query(ProfileInformation).filter(ProfileInformation.employee_id == employee_id).first():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Employee ID: {employee_id} not found")
 
        ## acept websocket and keep connection alive
        await websocket.accept()
        key = f"{employee_id}"
        employee_connections[key] = websocket
        print(f"Connection is open for {employee_id}")
 
 
        ## check is any data to send client
        with db_session() as db:
            
            data_to_send = [{"notification_id":data[0], "data":data[1]} for data in db.query(NotificationStorage.notification_id, NotificationStorage.data).filter(NotificationStorage.employee_id == employee_id)]
            print("############### Employee ######################")
            print(data_to_send)
            print("#########################################")
        if data_to_send:
            for content in data_to_send:
                await websocket.send_json(data=content)
 
        while True:
            data = await websocket.receive_json() ## keep the connection alive.
            print("############################################")
            print(data)
            print("############################################")
 
            ## need to get the sturcture like below.
            ## data = {"notification_id":1}
            ## remove the notification_id data at database
            if "notification_id" in data:
                with db_session() as db:
                    db.query(NotificationStorage).filter(NotificationStorage.notification_id == data["notification_id"]).delete()
                    db.commit()
 
    except WebSocketDisconnect:
        employee_connections.pop(key, None)
        print(employee_connections)
    except Exception as e:
        print(f"Exception at webscoket endpoint: {e}")
 
 


@router.websocket("/ws/candidate/{candidate_id}")
async def candidate_websocket_endpoint(
    candidate_id: str,
    websocket: WebSocket,
):
    try:
        # Validate candidate before accepting websocket
        with db_session() as db:
            if not db.query(CandidateProfileInformation).filter(
                CandidateProfileInformation.candidate_id == candidate_id
            ).first():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Candidate ID: {candidate_id} not found"
                )

        # Accept websocket and store connection
        await websocket.accept()
        key = f"{candidate_id}"
        candidate_connections[key] = websocket
        print(f"Connection is open for candidate {candidate_id}")

        # Send pending notifications (if any)
        with db_session() as db:
            data_to_send = [
                {
                    "notification_id": data[0],
                    "data": data[1]
                }
                for data in db.query(
                    CandidateNotificationStorage.notification_id,
                    CandidateNotificationStorage.data
                ).filter(
                    CandidateNotificationStorage.candidate_id == candidate_id
                )
            ]
            print("############### Candidate ######################")
            print(data_to_send)
            print("#########################################")

        if data_to_send:
            for content in data_to_send:
                await websocket.send_json(data=content)

        # Keep connection alive & wait for acknowledgement
        while True:
            data = await websocket.receive_json()
            # expected format:
            # data = {"notification_id": 1}

            if "notification_id" in data:
                with db_session() as db:
                    db.query(CandidateNotificationStorage).filter(
                        CandidateNotificationStorage.notification_id == data["notification_id"]
                    ).delete()
                    db.commit()

    except WebSocketDisconnect:
        candidate_connections.pop(key, None)
        print(f"Candidate disconnected: {candidate_id}")
        print(candidate_connections)

    except Exception as e:
        print(f"Exception at candidate websocket endpoint: {e}")


