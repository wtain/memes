import os

base_path = os.path.abspath("c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\")

base_path_unsorted = os.path.abspath("c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\Unsorted")

unsorted = set(file for file in os.listdir(base_path_unsorted))
print(f"Total in unsorted: {len(unsorted)}")

library = set(file for file in os.listdir(base_path))
print(f"Total in library: {len(library)}")

intersection = set.intersection(unsorted, library)
print(f"Intersection: {len(intersection)}")