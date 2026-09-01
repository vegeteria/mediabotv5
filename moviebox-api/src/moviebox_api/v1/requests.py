"""
Provide ways to interact with Moviebox using `httpx`
"""

import httpx
from httpx import Response
from httpx._config import DEFAULT_TIMEOUT_CONFIG
from httpx._types import (
    CookieTypes,
    ProxyTypes,
    TimeoutTypes,
)

from moviebox_api.v1.constants import DOWNLOAD_REQUEST_HEADERS
from moviebox_api.v1.exceptions import EmptyResponseError
from moviebox_api.v1.helpers import (
    get_absolute_url,
    process_api_response,
)
from moviebox_api.v1.models import MovieboxAppInfo

request_cookies = {}

__all__ = ["Session"]


class Session:
    """Performs actual get & post http requests asynchronously
    with or without cookies on demand
    """

    _moviebox_app_info_url = get_absolute_url(
        r"/wefeed-h5-bff/app/get-latest-app-pkgs?app_name=moviebox"
    )

    def __init__(
        self,
        headers: ProxyTypes | None = DOWNLOAD_REQUEST_HEADERS,
        cookies: CookieTypes | None = request_cookies,
        timeout: TimeoutTypes = DEFAULT_TIMEOUT_CONFIG,
        proxy: ProxyTypes | None = None,
        **httpx_kwargs,
    ):
        """Constructor for `Session`

        Args:
            headers (ProxyTypes | None, optional): Http request headers. Defaults to DOWNLOAD_REQUEST_HEADERS.
            cookies (CookieTypes | None , optional): Http request cookies. Defaults to request_cookies.
            timeout (TimeoutTypes, optional): Http request timeout in seconds. Defaults to DEFAULT_TIMEOUT_CONFIG.
            proxy (ProxyTypes | None, optional): Http requests proxy. Defaults to None.

        httpx_kwargs : Other keyword arguments for `httpx.AsyncClient`
        """  # noqa: E501
        self._headers = headers.copy() if headers else {}
        self._cookies = cookies
        self._timeout = timeout
        self._proxy = proxy

        self._client = httpx.AsyncClient(
            headers=headers,
            cookies=cookies,
            timeout=timeout,
            proxy=proxy,
            **httpx_kwargs,
        )

        self.moviebox_app_info: MovieboxAppInfo | None = None
        self.__moviebox_app_info_fetched: bool = False
        """Used to track cookies assignment status"""
        self._runtime_token: str | None = None

    def _generate_x_client_token(self) -> str:
        import time, hashlib
        ts = str(int(time.time()))
        r_ts = ts[::-1]
        h = hashlib.md5(r_ts.encode()).hexdigest()
        return f"{ts},{h}"

    def _update_headers_with_tokens(self, headers: dict) -> dict:
        headers = headers.copy()
        headers["X-Client-Token"] = self._generate_x_client_token()
        if self._runtime_token:
            headers["Authorization"] = f"Bearer {self._runtime_token}"
        return headers

    def _absorb_x_user(self, response: Response) -> None:
        import json
        x_user = response.headers.get("x-user")
        if x_user:
            try:
                data = json.loads(x_user)
                if token := data.get("token"):
                    self._runtime_token = token
            except json.JSONDecodeError:
                pass

    def _validate_response(self, response: Response) -> Response:
        """Ensures response is not empty"""
        self._absorb_x_user(response)
        if response is None or not bool(response.content):
            raise EmptyResponseError(
                response, "Server returned an empty body response."
            )
        return response

    def __repr__(self):
        return rf"<Session(MovieBoxAPI) timeout={self._timeout}>"

    async def _ensure_token(self):
        if not self._runtime_token:
            client = httpx.AsyncClient(
                headers=self._update_headers_with_tokens(self._headers),
                cookies=self._cookies,
                proxy=self._proxy,
                timeout=self._timeout,
            )
            resp = await client.get("https://h5-api.aoneroom.com/wefeed-h5api-bff/home?host=h5.aoneroom.com")
            self._absorb_x_user(resp)

    async def get(self, url: str, params: dict = {}, **kwargs) -> Response:
        """Makes a http get request without server cookies from previous requests.
        It's relevant because some requests with expired cookies won't go through
        but having it none does go through.

        Args:
            url (str): Resource link.
            params (dict, optional): Request params. Defaults to {}.

        Returns:
            Response: Httpx response object
        """
        await self._ensure_token()
        client = httpx.AsyncClient(
            headers=self._update_headers_with_tokens(self._headers),
            cookies=self._cookies,
            proxy=self._proxy,
            timeout=self._timeout,
            **kwargs,
        )
        response = await client.get(url, params=params)
        response.raise_for_status()
        return self._validate_response(response)

    async def get_from_api(self, *args, **kwargs) -> dict:
        """Fetch data from api and extract the `data` field from the response

        Returns:
            dict: Extracted data field value
        """
        response = await self.get(*args, **kwargs)
        return process_api_response(response)

    async def get_with_cookies(
        self, url: str, params: dict = {}, **kwargs
    ) -> Response:
        """Makes a http get request with server-assigned cookies from previous
          requests.

        Args:
            url (str): Resource link.
            params (dict, optional): Request params. Defaults to {}.

        Returns:
            Response: Httpx response object
        """
        await self.ensure_cookies_are_assigned()
        await self._ensure_token()

        response = await self._client.get(url, params=params, headers=self._update_headers_with_tokens({}), **kwargs)
        response.raise_for_status()

        return self._validate_response(response)

    async def get_with_cookies_from_api(self, *args, **kwargs) -> dict:
        """Makes a http get request with server-assigned cookies from previous
        requests and extract the `data` field from the response.

        Returns:
            dict: Extracted data field value
        """
        response = await self.get_with_cookies(*args, **kwargs)
        return process_api_response(response)

    async def post(self, url: str, json: dict, **kwargs) -> Response:
        """Makes a http post request with both self assigned and server-
        assigned cookies

        Args:
            url (str): Resource link
            json (dict): Data to be send.

        Returns:
            Response: Httpx response object
        """
        await self.ensure_cookies_are_assigned()
        await self._ensure_token()

        response = await self._client.post(url, json=json, headers=self._update_headers_with_tokens({}), **kwargs)
        response.raise_for_status()

        return self._validate_response(response)

    async def post_to_api(self, *args, **kwargs) -> dict:
        """Sends data to api and extract the `data` field from the response

        Returns:
            dict: Extracted data field value
        """
        response = await self.post(*args, **kwargs)
        return process_api_response(response)

    async def ensure_cookies_are_assigned(self) -> bool:
        """Checks if the essential cookies are available if not update it.

        Returns:
            bool: `account` cookie availability status.
        """
        if not self.__moviebox_app_info_fetched:
            # First run probably
            await self._fetch_app_info()
            self.__moviebox_app_info_fetched = True

        return self._client.cookies.get("account") is not None

    async def _fetch_app_info(self) -> MovieboxAppInfo:
        """Fetches the moviebox app info but the main goal is to get the essential
          cookies required for requests such as download to go through.

        Returns:
            MovieboxAppInfo: Details about latest moviebox app
        """
        await self._ensure_token()
        response = await self._client.get(url=self._moviebox_app_info_url, headers=self._update_headers_with_tokens({}))
        response.raise_for_status()

        moviebox_app_info = process_api_response(response)

        if isinstance(moviebox_app_info, list):
            moviebox_app_info = moviebox_app_info[0]

        self.moviebox_app_info = MovieboxAppInfo(**moviebox_app_info)

        return self.moviebox_app_info

    update_session_cookies = _fetch_app_info
