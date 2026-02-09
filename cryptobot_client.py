import asyncio
from os import getenv
from dotenv import load_dotenv

from cryptobot import CryptoBotClient
from cryptobot.models import Asset, ButtonName, Status

load_dotenv()


class CryptoBotService:
    def __init__(self, api_token: str | None = None, is_mainnet: bool = True, timeout: float = 5.0):
        token = api_token or getenv("CRYPTOBOT_API_TOKEN")
        if not token:
            raise RuntimeError("Missing Crypto Bot API token. Set CRYPTOBOT_API_TOKEN in .env")
        self._client = CryptoBotClient(token, is_mainnet=is_mainnet, timeout=timeout)

    @property
    def client(self) -> CryptoBotClient:
        return self._client

    async def get_me(self):
        return await asyncio.to_thread(self._client.get_me)

    async def create_invoice(
        self,
        asset: Asset,
        amount: float,
        description: str | None = None,
        hidden_message: str | None = None,
        paid_btn_name: ButtonName | None = None,
        paid_btn_url: str | None = None,
        payload: str | None = None,
        allow_comments: bool | None = None,
        allow_anonymous: bool | None = None,
        expires_in: int | None = None,
        swap_to: str | None = None,
    ):
        return await asyncio.to_thread(
            self._client.create_invoice,
            asset=asset,
            amount=amount,
            description=description,
            hidden_message=hidden_message,
            paid_btn_name=paid_btn_name,
            paid_btn_url=paid_btn_url,
            payload=payload,
            allow_comments=allow_comments,
            allow_anonymous=allow_anonymous,
            expires_in=expires_in,
            swap_to=swap_to,
        )

    async def get_invoices(
        self,
        asset: Asset | None = None,
        invoice_ids: str | None = None,
        status: Status | None = None,
        offset: int = 0,
        count: int = 100,
    ):
        return await asyncio.to_thread(
            self._client.get_invoices,
            asset,
            invoice_ids,
            status,
            offset,
            count,
        )

    async def get_balances(self):
        return await asyncio.to_thread(self._client.get_balances)

    async def get_exchange_rates(self):
        return await asyncio.to_thread(self._client.get_exchange_rates)

    async def transfer(
        self,
        user_id: int,
        asset: Asset,
        amount: float,
        spend_id: str,
        comment: str | None = None,
        disable_send_notification: bool = False,
    ):
        return await asyncio.to_thread(
            self._client.transfer,
            user_id,
            asset,
            amount,
            spend_id,
            comment,
            disable_send_notification,
        )
