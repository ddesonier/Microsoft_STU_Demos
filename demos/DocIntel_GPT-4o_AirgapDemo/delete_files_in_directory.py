import os

def delete_files_in_directory(directory_path):
    """
    Deletes all files and subdirectories in the specified directory.

    Args:
        directory_path (str): The path to the directory to be cleaned.
    """
    for filename in os.listdir(directory_path):
        file_path = os.path.join(directory_path, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print("Failed")
            #print(f'Failed to delete {file_path}. Reason: {e}')