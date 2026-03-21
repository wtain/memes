from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import requests
import os
import time


"""

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import requests
import time

driver = uc.Chrome()
# Rest of the code similar to above...

"""

def save_google_images(search_query, num_images=10, download_folder="downloads"):
    """
    Search Google Images and download images
    """
    # Create download folder
    os.makedirs(download_folder, exist_ok=True)

    # Setup Chrome options
    options = webdriver.ChromeOptions()
    options.add_experimental_option("prefs", {
        "download.default_directory": os.path.abspath(download_folder),
        "profile.default_content_setting_values.automatic_downloads": 1
    })

    driver = webdriver.Chrome(options=options)

    try:
        # Navigate to Google Images
        driver.get("https://images.google.com")

        # Search for query
        search_box = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "q"))
        )
        search_box.send_keys(search_query)
        search_box.send_keys(Keys.RETURN)

        # Wait for results
        time.sleep(2)

        downloaded = 0
        image_index = 0

        while downloaded < num_images:
            try:
                # Find all visible image thumbnails
                # Google Images uses data attributes for image containers
                images = driver.find_elements(By.CSS_SELECTOR, "img.rg_i.Q4LuWd")

                # Get current visible images
                if image_index < len(images):
                    img = images[image_index]

                    # Scroll to image
                    driver.execute_script("arguments[0].scrollIntoView(true);", img)
                    time.sleep(0.5)

                    # Click to open large version
                    img.click()
                    time.sleep(1)

                    # Wait for large image panel and get actual image URL
                    # Try multiple selectors for the large image
                    large_img = None
                    for selector in [
                        "img.n3VNCb",  # Current Google Images class
                        "div[data-attrid='image'] img",
                        ".v4dQwb img"
                    ]:
                        try:
                            large_img = WebDriverWait(driver, 5).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                            )
                            if large_img:
                                break
                        except:
                            continue

                    if large_img:
                        img_url = large_img.get_attribute("src")

                        # Download image
                        if img_url and not img_url.startswith("data:"):
                            response = requests.get(img_url, stream=True)
                            if response.status_code == 200:
                                filename = f"{search_query.replace(' ', '_')}_{downloaded + 1}.jpg"
                                filepath = os.path.join(download_folder, filename)

                                with open(filepath, 'wb') as f:
                                    for chunk in response.iter_content(1024):
                                        f.write(chunk)

                                downloaded += 1
                                print(f"Downloaded {downloaded}/{num_images}: {filename}")

                    image_index += 1

                    # Scroll to load more results when needed
                    if image_index >= len(images) - 5:
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        time.sleep(2)

                else:
                    # Load more images
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(2)

            except Exception as e:
                print(f"Error on image {image_index}: {e}")
                image_index += 1
                continue

    finally:
        driver.quit()


# Usage
save_google_images("cute cats", num_images=5)