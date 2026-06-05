import asyncio

from Storage.db import AsyncSessionLocal
from repository.ocr_text import OCRTextRepository


async def main():
    async with AsyncSessionLocal() as session:
        ocr_repo = OCRTextRepository(session)

        total_before = await ocr_repo.count_texts()
        print(f"Total OCR texts before: {total_before}")

        print("Removing duplicates...")
        removed = await ocr_repo.delete_duplicate_texts()

        await session.commit()

    print(f"Removed:   {removed}")
    print(f"Remaining: {total_before - removed}")


if __name__ == "__main__":
    asyncio.run(main())