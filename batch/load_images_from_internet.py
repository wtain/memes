import asyncio

import aiohttp
from dotenv import load_dotenv
from serpapi import GoogleSearch
import os

load_dotenv()

SERP_API_KEY = os.getenv('SERP_API_KEY')

def normalize_query(q: str) -> str:
    return q.lower().replace(" ", "-")


def get_extension(content_type: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    return mapping.get(content_type.split(";")[0].lower(), ".bin")


def fetch_results(query, max_images=100):
    params = {
        "q": query,
        "tbm": "isch",
        "api_key": SERP_API_KEY,
    }

    search = GoogleSearch(params)
    results = search.get_dict()

    return results.get("images_results", [])[:max_images]


async def download_image(session, url, path, semaphore):
    async with semaphore:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return

                content_type = resp.headers.get("Content-Type", "")
                ext = get_extension(content_type)

                data = await resp.read()

                with open(path + ext, "wb") as f:
                    f.write(data)

        except Exception as e:
            print(f"Error loading {url}: {e}")


async def download_images(query, images, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    semaphore = asyncio.Semaphore(10)  # tune this

    async with aiohttp.ClientSession() as session:
        tasks = []

        for i, img in enumerate(images):
            url = img.get("original")
            if not url:
                continue

            filename = f"{normalize_query(query)}_{i}"
            path = os.path.join(output_dir, filename)

            tasks.append(download_image(session, url, path, semaphore))

        await asyncio.gather(*tasks)


async def main():
    for query in [

    ]:
        print(f"Running query for '{query}'")
        images = fetch_results(query, max_images=100)

        directory_name = normalize_query(query)

        await download_images(query, images, f"images/{directory_name}")


if __name__ == "__main__":
    asyncio.run(main())
