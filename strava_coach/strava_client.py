import time
from typing import Iterator, Optional

import httpx

API_BASE = "https://www.strava.com/api/v3"


class StravaClient:
    def __init__(self, access_token: str):
        self._client = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )

    def _get(self, path: str, params: Optional[dict] = None) -> httpx.Response:
        for _ in range(5):
            resp = self._client.get(path, params=params)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "15"))
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    def list_activities(self, after: Optional[int] = None, per_page: int = 100) -> Iterator[dict]:
        page = 1
        while True:
            params = {"per_page": per_page, "page": page}
            if after is not None:
                params["after"] = after
            batch = self._get("/athlete/activities", params=params).json()
            if not batch:
                return
            for activity in batch:
                if activity.get("type") == "Run":
                    yield activity
            page += 1

    def get_laps(self, activity_id: int) -> list[dict]:
        return self._get(f"/activities/{activity_id}/laps").json()

    def get_activity_detail(self, activity_id: int) -> dict:
        return self._get(f"/activities/{activity_id}").json()

    def get_activity_zones(self, activity_id: int) -> list:
        return self._get(f"/activities/{activity_id}/zones").json()

    def get_athlete_zones(self) -> dict:
        return self._get("/athlete/zones").json()

    def get_streams(self, activity_id: int) -> dict:
        keys = "time,distance,heartrate,velocity_smooth,altitude,latlng"
        return self._get(
            f"/activities/{activity_id}/streams",
            params={"keys": keys, "key_by_type": "true"},
        ).json()
