import asyncio
from collections import Counter
import re

from sqlalchemy import select, exists

from Storage.db import AsyncSessionLocal
from Storage.models import Image, ImageDescription, ImageTag


def extract_words(text):
    """
    Extract words from text, removing special characters, digits, punctuation,
    and convert to lowercase.

    Args:
        text (str): Input text string

    Returns:
        list: List of cleaned words
    """
    # Use regex to find all sequences of alphabetic characters
    words = re.findall(r'[a-zA-Z]+', text)

    # Convert each word to lowercase
    words = [word.lower() for word in words]

    return words


async def main():

    async with AsyncSessionLocal() as session:
        query = (
            select(
                ImageDescription.text
            )
            .where(
                ImageDescription.image_id.in_(
                    select(Image.id)
                    .where(
                        ~(
                            exists()
                            .where(ImageTag.image_id == Image.id)
                        )
                    )
                )
            )
        )
        texts = await session.execute(query)

        stop_words = {
            "the", "a", "of", "me", "to", "and", "is", "with", "in", "that", "or", "on", "are", "be", "an", "there",
            "text", "videos", "image",
            "bla",
            "people", "person",
            "while", "like", "their", "reads", "at", "by", "they", "for", "from",
            "appears", "humorous",
            "which", "as",
            "it", "meeeme", "s", "mmbr", "shows", "mean", "this", "two",
            "omm", "you", "who", "has", "says", "what", "i", "seems", "one",
            "suggesting", "caption", "might", "suggests", "top", "another", "right", "left", "t", "features",
            "about", "not", "someone", "my", "different", "but",
            "he", "his", "when", "omn", "other", "where", "them", "also", "some", "first", "than", "m", "now",
            "then", "guy", "persona", "self", "had", "la", "th",
            "all", "re", "we", "no", "c", "e", "lot",
            "have", "between", "both", "she", "may", "these", "been", "was", "such", "mr",
            "sx", "am", "n", "de", "esa", "dame", "g", "w", "z",
            "being", "don", "can", "mee", "more", "how", "kee", "omnn", "mmmmbbrr", "each", "dii", "rahhht", "her",
        }

        count = 0
        counter = Counter()
        for (text, ) in texts:
            for word in extract_words(text):
                if len(word) < 3 or word in stop_words:
                    continue
                counter[word] += 1
            count += 1

        print(f"Total untagged: {count}")

        bag_of_words = list(sorted([(word, frequency) for word, frequency in counter.items() if frequency > 10], key=lambda tuple: -tuple[1]))

        for word, frequency in bag_of_words:
            print(f"{word} ({frequency})")

if __name__ == "__main__":
    asyncio.run(main())