import subprocess
import os
import sys
import time
import glob
import requests
from tqdm import tqdm
import threading

ATTEMPTS = 3

def send_message(title, message):
    """Send a notification"""
    requests.post("https://api.example.com/Movies",
    data=message,
    headers={
        "Title": title,
    })

def error_handler(message):
    """Handle unrecoverable errors by printing, sending notification, and exiting."""
    print(message)
    try:
        send_message("Movie ERROR", message)
    except Exception as e:
        print("Failed to send error notification:", e)
    sys.exit(1)

def get_largest_title(drive_letter):
    """
    Run MakeMKV to get disc information and determine the title with the largest file size.
    Raises an Exception if something goes wrong.
    """
    try:
        result = subprocess.run(
            ["./makemkvcon64.exe", "-r", "info", f"dev:{drive_letter}:"],
            capture_output=True, text=True
        )
    except Exception as e:
        raise Exception("Error running MakeMKV: " + str(e))
    
    if result.returncode != 0 or not result.stdout:
        raise Exception("MakeMKV returned an error. Please ensure MakeMKV is installed and the DVD is readable.")
    
    disc_info_output = result.stdout
    # Filter for lines starting with "TINFO:" and process them.
    tinfo_lines = [line.strip() for line in disc_info_output.splitlines() if line.strip().startswith("TINFO:")]
    largest_title_index = -1
    largest_title_size = 0

    for line in tinfo_lines:
        parts = line.split(",")
        if len(parts) >= 4:
            title_index_str = parts[0].replace("TINFO:", "").strip()
            info_type = parts[1].strip()
            if info_type == "11":  # file size in bytes
                size_str = parts[3].strip().strip('"')
                try:
                    file_size = int(size_str)
                except ValueError:
                    file_size = 0
                if file_size > largest_title_size:
                    largest_title_size = file_size
                    largest_title_index = title_index_str

    if largest_title_index == -1:
        raise Exception("Could not find any titles on the DVD.")

    return largest_title_index, largest_title_size

def poll_file_size_progress(expected_file_size, output_directory, progress_bar, stop_event):
    """
    Polls the file size of the MKV file being created and updates the progress bar.
    Runs until stop_event is set.
    """
    current_progress = 0
    while not stop_event.is_set():
        mkv_files = glob.glob(os.path.join(output_directory, "*.mkv"))
        if mkv_files:
            # Assume the largest file is the one being written.
            current_file = max(mkv_files, key=os.path.getsize)
            try:
                progress = os.path.getsize(current_file)
                if progress > current_progress:
                    progress_bar.n = progress
                    progress_bar.refresh()
                    current_progress = progress
            except Exception as e:
                print("Error checking file size:", e)
        time.sleep(1)

def rip_dvd(title_index, output_file_name, output_directory, expected_file_size, drive_letter):
    """
    Rip the DVD title using MakeMKV while concurrently monitoring the file size to update a progress bar.
    """
    command = ["./makemkvcon64.exe", "-r", "mkv", f"dev:{drive_letter}:", title_index, output_directory]
    
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    
    progress_bar = tqdm(total=expected_file_size, desc="Ripping DVD", unit="%", dynamic_ncols=True)
    stop_event = threading.Event()
    
    monitor_thread = threading.Thread(
        target=poll_file_size_progress,
        args=(expected_file_size, output_directory, progress_bar, stop_event)
    )
    monitor_thread.start()
    
    # Optionally, process and discard output for a cleaner console:
    for line in process.stdout:
        pass  # Suppress output
    
    process.wait()
    stop_event.set()
    monitor_thread.join()
    
    progress_bar.n = expected_file_size
    progress_bar.refresh()
    progress_bar.close()
    
    if process.returncode != 0:
        raise Exception("MakeMKV returned an error during ripping.")
    
    # Locate the final MKV file
    mkv_files = sorted(glob.glob(os.path.join(output_directory, "*.mkv")),
                       key=os.path.getmtime, reverse=True)
    if not mkv_files:
        raise Exception("No MKV file found after ripping.")
    
    ripped_file = mkv_files[0]
    new_file_name = f"{output_file_name}.mkv"
    new_file_path = os.path.join(output_directory, new_file_name)
    try:
        os.rename(ripped_file, new_file_path)
    except Exception as e:
        raise Exception("Error renaming MKV file: " + str(e))
    
    return new_file_path

def encode_to_mp4(new_file_path, output_file_name, output_directory):
    """
    Convert the ripped MKV file to MP4 using HandBrakeCLI.
    Raises an Exception if the conversion fails.
    """
    handbrake_preset_file = "./Movies.json"
    handbrake_preset = "Movies"
    mp4_output_file = os.path.join(output_directory, f"{output_file_name}.mp4")

    print(f"Converting {new_file_path} to MP4 using HandBrake...")
    handbrake_args = [
        "./HandBrakeCLI",
        "--preset-import-file", handbrake_preset_file,
        "--preset", handbrake_preset,
        "-i", new_file_path,
        "-o", mp4_output_file
    ]
    try:
        subprocess.run(handbrake_args, check=True)
    except subprocess.CalledProcessError as e:
        raise Exception("HandBrakeCLI returned an error during conversion: " + str(e))
    
    return mp4_output_file

def run(function, *args, **kwargs):
    for i in range(1, ATTEMPTS + 1):
        try:
            output = function(*args, **kwargs)
        except Exception as e:
            if i < ATTEMPTS:
                continue
            else:
                error_handler(f"Error running: {function.__name__}\nAttempts: {ATTEMPTS}\nError: {e}")
        return output

def main():
    output_file_name = input("Enter the output file name (without extension): ").strip()
    drive_letter = input("Enter the DVD drive letter (without colon): ").strip()
    
    output_directory = os.path.join("temp", drive_letter)
    if not os.path.exists(output_directory):
        try:
            os.makedirs(output_directory)
        except Exception as e:
            error_handler("Error creating temp directory: " + str(e))
    
    print("Getting disc info from MakeMKV...")
    largest_title_index, largest_title_size = run(get_largest_title, drive_letter)
    largest_title_size_gb = round(largest_title_size / 1073741824, 2)
    print(f"Largest title found: Title {largest_title_index} with size: {largest_title_size_gb} GB")

    print(f"Ripping largest title (Title {largest_title_index}) to output directory...")
    start = time.time()
    new_file_path = run(rip_dvd, largest_title_index, output_file_name, output_directory, largest_title_size, drive_letter)
    rip_end = time.time()

    try:
        send_message("Movies", f"Movie {output_file_name} has been ripped!\nDuration: {time.time() - rip_end} seconds")
    except Exception as e:
        print("Warning: Failed to send rip notification:", e)
    
    mp4_output_file = run(encode_to_mp4, new_file_path, output_file_name, output_directory)
    encode_end = time.time()

    print(f"Conversion completed successfully! MP4 file saved as: {mp4_output_file}")
    
    try:
        send_message("Movies", f"The movie {output_file_name} has been ripped and encoded\n")
    except Exception as e:
        print("Warning: Failed to send encoding notification:", e)
    
    delete_mkv = input("Do you want to delete the MKV file? (yes/no): ").strip().lower()
    if delete_mkv == "yes":
        try:
            os.remove(new_file_path)
            print("MKV file deleted.")
        except Exception as e:
            print("Error deleting MKV file:", e)

        # move mp4 file to movies folder
        try:
            os.rename(mp4_output_file, f"output/{output_file_name}.mp4")
        except Exception as e:
            print("Error moving MP4 file:", e)

        # remove all files in original output directory
        try:
            for file in os.listdir(output_directory):
                os.remove(os.path.join(output_directory, file))
            os.rmdir(output_directory)
        except Exception as e:
            print("Error cleaning up output directory:", e)

    print("Restarting the program...")
    time.sleep(2)
    os.system('cls' if os.name == 'nt' else 'clear')
    main()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            send_message("Movie ERROR", "Unexpected error")
        except Exception as send_err:
            print("Failed to send error notification:", send_err)
        sys.exit(1)
