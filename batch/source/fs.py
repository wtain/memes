import os


class LocalFileSystemFolder:

    def __init__(self, base_path):
        self.base_path = base_path

    def check_exists(self, filename):
        path = os.path.join(self.base_path, filename)
        return os.path.exists(path)

    def check_is_directory(self, filename):
        path = os.path.join(self.base_path, filename)
        return os.path.isdir(path)