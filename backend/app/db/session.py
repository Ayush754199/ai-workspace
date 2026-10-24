from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from app.core.config import settings


class MongoCollections:
    def __init__(self) -> None:
        if not settings.MONGODB_URL:
            self.client = None
            self.database = None
            self.users = None
            self.chat_history = None
            return
        self.client = AsyncIOMotorClient(settings.MONGODB_URL, serverSelectionTimeoutMS=5000)
        self.database = self.client[settings.MONGODB_DATABASE]
        self.users: AsyncIOMotorCollection = self.database["users"]
        self.chat_history: AsyncIOMotorCollection = self.database["chat_history"]

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def close(self) -> None:
        if self.client is not None:
            self.client.close()


mongo = MongoCollections()