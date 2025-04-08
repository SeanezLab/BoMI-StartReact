import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import argparse
import csv
from typing import List, Dict

class TrignoAuxVisualizer:
    def __init__(self, csv_path: str, sensor_ids: List[int]):
        self.sensor_ids = sensor_ids
        self.channels_per_sensor = 9 # 3 ACC + 3 GYR + 3 empty
        
        # Create mapping of sensor IDs to their column indices
        self.sensor_columns = {
            sensor_id: 1 + (sensor_id - 1) * self.channels_per_sensor 
            for sensor_id in sensor_ids
        }
        print(self.sensor_columns)
        # Read CSV data
        print(f"Loading data for sensors {sensor_ids}...")
        self.timestamps = []
        self.sensor_data = {sensor_id: [] for sensor_id in sensor_ids}
        
        with open(csv_path, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                # Convert all strings to floats
                values = [float(x) for x in row]
                self.timestamps.append(values[0])
                
                # Get data for each requested sensor
                for sensor_id in sensor_ids:
                    start_idx = self.sensor_columns[sensor_id]
                    sensor_values = values[start_idx:start_idx + self.channels_per_sensor]
                    self.sensor_data[sensor_id].append(sensor_values)
        
        # Convert to numpy arrays for efficient slicing
        self.timestamps = np.array(self.timestamps)
        self.sensor_data = {
            sensor_id: np.array(data) 
            for sensor_id, data in self.sensor_data.items()
        }
            
        # Setup plotting
        self.fig, self.axes = plt.subplots(len(sensor_ids), 2, figsize=(15, 5*len(sensor_ids)))
        if len(sensor_ids) == 1:
            self.axes = np.array([self.axes])  # Make 2D array for consistent indexing
        
        # Initialize lines for each sensor
        self.acc_lines = {}
        self.gyr_lines = {}
        self.window_size = 100  # Number of points to show
        self.frame_interval = 1000/74  # 74 Hz in milliseconds
        
        for idx, sensor_id in enumerate(sensor_ids):
            # Accelerometer plot
            acc_lines = self.axes[idx, 0].plot([], [], 'r-', [], [], 'g-', [], [], 'b-')
            self.acc_lines[sensor_id] = acc_lines
            self.axes[idx, 0].set_title(f'Sensor {sensor_id} Accelerometer')
            self.axes[idx, 0].set_xlabel('Time (s)')
            self.axes[idx, 0].set_ylabel('Acceleration')
            self.axes[idx, 0].legend(['X', 'Y', 'Z'])
            self.axes[idx, 0].grid(True)
            
            # Gyroscope plot
            gyr_lines = self.axes[idx, 1].plot([], [], 'r-', [], [], 'g-', [], [], 'b-')
            self.gyr_lines[sensor_id] = gyr_lines
            self.axes[idx, 1].set_title(f'Sensor {sensor_id} Gyroscope')
            self.axes[idx, 1].set_xlabel('Time (s)')
            self.axes[idx, 1].set_ylabel('Angular Velocity')
            self.axes[idx, 1].legend(['X', 'Y', 'Z'])
            self.axes[idx, 1].grid(True)
        
        plt.tight_layout()
        
        # Animation state
        self.current_idx = 0
        
    def init_animation(self):
        """Initialize animation"""
        for acc_line_set in self.acc_lines.values():
            for line in acc_line_set:
                line.set_data([], [])
        for gyr_line_set in self.gyr_lines.values():
            for line in gyr_line_set:
                line.set_data([], [])
        return []
    
    def update(self, frame):
        """Update animation frame"""
        # Update window indices
        start_idx = max(0, self.current_idx - self.window_size)
        end_idx = self.current_idx
        
        # Time values for x-axis
        time_window = self.timestamps[start_idx:end_idx] - self.timestamps[start_idx]
        
        # Update each sensor's plots
        for idx, sensor_id in enumerate(self.sensor_ids):
            sensor_data = self.sensor_data[sensor_id]
            
            # Update accelerometer lines
            for j in range(3):
                self.acc_lines[sensor_id][j].set_data(
                    time_window,
                    sensor_data[start_idx:end_idx, j]
                )
            
            # Update gyroscope lines
            for j in range(3):
                self.gyr_lines[sensor_id][j].set_data(
                    time_window,
                    sensor_data[start_idx:end_idx, j + 3]
                )
            
            # Adjust plot limits
            if end_idx > start_idx:
                self.axes[idx, 0].set_xlim(0, time_window[-1])
                self.axes[idx, 0].set_ylim(
                    np.min(sensor_data[start_idx:end_idx, :3]) * 1.1,
                    np.max(sensor_data[start_idx:end_idx, :3]) * 1.1
                )
                
                self.axes[idx, 1].set_xlim(0, time_window[-1])
                self.axes[idx, 1].set_ylim(
                    np.min(sensor_data[start_idx:end_idx, 3:]) * 1.1,
                    np.max(sensor_data[start_idx:end_idx, 3:]) * 1.1
                )
        
        self.current_idx += 1
        if self.current_idx >= len(self.timestamps):
            self.current_idx = 0
            
        return []
    
    def animate(self):
        """Start the animation"""
        print("Starting animation...")
        anim = FuncAnimation(
            self.fig, 
            self.update,
            init_func=self.init_animation,
            interval=self.frame_interval,
            blit=True
        )
        plt.show()

def parse_sensor_list(sensor_str: str) -> List[int]:
    """Parse comma-separated sensor IDs into a list of integers"""
    try:
        return [int(x.strip()) for x in sensor_str.split(',')]
    except ValueError:
        raise argparse.ArgumentTypeError("Sensor IDs must be comma-separated integers")

def main():
    parser = argparse.ArgumentParser(description='Visualize Trigno AUX data from CSV')
    parser.add_argument('csv_file', type=str, help='Path to CSV file')
    parser.add_argument('sensors', type=parse_sensor_list, 
                       help='Comma-separated list of sensor IDs (e.g., "9,10,16")')
    args = parser.parse_args()
    
    visualizer = TrignoAuxVisualizer(args.csv_file, args.sensors)
    visualizer.animate()

if __name__ == "__main__":
    main()