import os

DIRECTORY = "/home/a/akashsingh/BRCA-Restaging/gdc_downloads"
OUTPUT_FILE = "folders_output.txt"


def list_folders(directory, output_file):
    folders = [
        name for name in os.listdir(directory)
        if os.path.isdir(os.path.join(directory, name))
    ]
    folders.sort()

    with open(output_file, "w") as f:
        for folder in folders:
            f.write(folder + "\n")

    print(f"Found {len(folders)} folder(s). Written to: {output_file}")


if __name__ == "__main__":
    list_folders(DIRECTORY, OUTPUT_FILE)
