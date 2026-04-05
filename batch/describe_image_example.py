import ollama

"""
https://ollama.com/download

Windows
irm https://ollama.com/install.ps1 | iex

ollama pull llava

"""

# Путь к вашему изображению
# image_path = 'c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\Screenshot_20240928_075014_Instagram.jpg'
images_paths = [
    'c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\Screenshot_20240928_112232_Instagram.jpg',
    'c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\Screenshot_20240928_075014_Instagram.jpg',
]

for image_path in images_paths:
    response = ollama.chat(
        model='llava',
        messages=[{
            'role': 'user',
            # 'content': 'Что изображено на этом фото? Опиши подробно.',
            # 'content': 'What is shown in this image?',
            'content':
# """
#             Analyze this image.
#
# Return:
# 1. Short description
# 2. Meme template (if any)
# 3. Emotions conveyed
# 4. Possible concepts/tags
#
# Be concise and structured.
#             """,
                """
                Analyze this image.
            
                Return:
                1. Short description
                2. Meme template (if any)
                3. Emotions conveyed
                4. Possible concepts/tags
            
                Be concise and structured.
                Return result as a JSON:
                {
                    "description": "...",
                    "template": "...",
                    "emotions": [...],
                    "concepts": [...],
                    "tags": [...]
                }
                Tags examples:
                band:metallica, person:james hetfield 
                """,
            'images': [image_path]
        }]
    )

    print(response['message']['content'])
