from repository.image_descriptions import ImageDescriptionsRepository


class DescriptionBatchCommitter:
    def __init__(self, session, batch_size: int = 100):
        self._session = session
        self._repo = ImageDescriptionsRepository(session)
        self._batch_size = batch_size
        self._pending = 0

    def save_description(self, image_id, prompt_key: str, model_used: str, text: str) -> None:
        self._repo.save(image_id, prompt_key, model_used, text)

    async def on_image_done(self) -> None:
        self._pending += 1
        if self._pending >= self._batch_size:
            await self.flush()

    async def flush(self) -> None:
        await self._session.commit()
        self._pending = 0

    async def close(self) -> None:
        if self._pending > 0:
            await self.flush()
        await self._session.close()
