import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, Wedge

def plot_circular_gauge(angle_inner, angle_outer, radius=1):
    fig, ax = plt.subplots()

    # Adjust starting ratios for arrow positions
    inner_start_ratio = 0.7
    inner_end_radio = 0.7001
    outer_start_ratio = 1
    outer_end_ratio = 0.9999

    label_ratio = 1.08

    # Adjust angles for clockwise direction with 0 at the top
    angle_inner_adjusted = (450 - angle_inner) % 360
    angle_outer_adjusted = (450 - angle_outer) % 360

    # Determine the correct sector to fill
    if angle_inner_adjusted < angle_outer_adjusted:
        start_angle = angle_inner_adjusted
        end_angle = angle_outer_adjusted
    else:
        start_angle = angle_outer_adjusted
        end_angle = angle_inner_adjusted

    # Correct filling between angles, ensuring no crossing over 0°
    if angle_inner_adjusted > angle_outer_adjusted:
        start_angle, end_angle = end_angle, start_angle  # Swap if the order is reversed

    # Adjust the angles for the wedge
    theta1 = (450 - angle_outer) % 360
    theta2 = (450 - angle_inner) % 360

    # Paint zone between angles
    wedge = Wedge((0, 0), radius * 0.9, theta1, theta2, width=radius * 0.1, color='orange', alpha=0.5)
    ax.add_patch(wedge)


    # Add the circular gauges
    # gauge1 = Circle((0, 0), radius * 0.9, edgecolor='black', facecolor='none', linewidth=1.5)
    # gauge2 = Circle((0, 0), radius * 0.8, edgecolor='black', facecolor='none', linewidth=1.5)
    #
    # ax.add_patch(gauge1)
    # ax.add_patch(gauge2)

    # Adjust angles to start from the top and increase clockwise
    # Subtracting from 450 degrees (or adding to 90 degrees) to rotate the coordinate system
    angle_inner_rad = np.deg2rad((450 - angle_inner) % 360)
    angle_outer_rad = np.deg2rad((450 - angle_outer) % 360)

    # Calculate arrow positions based on adjusted angles
    inner_arrow_start_x = radius * inner_start_ratio * np.cos(angle_inner_rad)
    inner_arrow_start_y = radius * inner_start_ratio * np.sin(angle_inner_rad)
    outer_arrow_start_x = radius * outer_start_ratio * np.cos(angle_outer_rad)
    outer_arrow_start_y = radius * outer_start_ratio * np.sin(angle_outer_rad)



    # Arrows
    # Inner arrow (shorter, starting further in)
    ax.arrow(inner_arrow_start_x, inner_arrow_start_y, (inner_end_radio - inner_start_ratio) * np.cos(angle_inner_rad), (inner_end_radio - inner_start_ratio) * np.sin(angle_inner_rad), head_width=0.09, head_length=0.1, fc='blue', ec='blue')
    # Outer arrow (starting from the edge, pointing inward)
    ax.arrow(outer_arrow_start_x, outer_arrow_start_y, -(outer_start_ratio - outer_end_ratio) * np.cos(angle_outer_rad), -(outer_start_ratio - outer_end_ratio) * np.sin(angle_outer_rad), head_width=0.09, head_length=0.1, fc='red', ec='red')

    # Text labels for angles
    label_y_offset = 0.1  # Vertical offset between labels
    ax.text(0, 0.3, f'Antenna', color='blue', fontsize=14, ha='center', va='center')
    ax.text(0, 0.1, f'{angle_inner:06.2f}°', color='blue', fontsize=26, ha='center', va='center', weight="bold")

    ax.text(0, -0.1, f'{angle_outer:06.2f}°', color='red', fontsize=26, ha='center', va='center', weight="bold")
    ax.text(0, -0.28, f'Target', color='red', fontsize=14, ha='center', va='center')


    # Set plot limits and aspect ratio
    ax.set_xlim(-radius * 1.1, radius * 1.1)
    ax.set_ylim(-radius * 1.1, radius * 1.1)
    ax.set_aspect('equal')

    # Add angle ticks and labels, adjusting for clockwise rotation from the top
    for angle in range(0, 360, 5):
        width = 1.5 if angle % 10 == 0 else 0.5
        adjusted_angle = (450 - angle) % 360
        angle_rad = np.deg2rad(adjusted_angle)

        start_x = radius * (inner_start_ratio + 0.1) * np.cos(angle_rad)
        start_y = radius * (inner_start_ratio + 0.1) * np.sin(angle_rad)
        end_x = radius * (outer_start_ratio - 0.1) * np.cos(angle_rad)
        end_y = radius * (outer_start_ratio - 0.1) * np.sin(angle_rad)
        ax.plot([start_x, end_x], [start_y, end_y], 'k', linewidth=width)  # Tick line

        # Label every 30 degrees
        if angle % 30 == 0:
            label_x = radius * label_ratio * np.cos(angle_rad)
            label_y = radius * label_ratio * np.sin(angle_rad)
            ax.text(label_x, label_y, str(angle) + '°', ha='center', va='center')

    # Remove axis
    ax.axis('off')

    plt.show()

# Example usage
plot_circular_gauge(30, 120, radius=1)





# import asyncio, qasync, functools
# import traceback
# import sys
# from datetime import datetime, timedelta
#
# from PyQt5 import QtWidgets
# from PyQt5.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QApplication
# from PyQt5.QtCore import Qt
#
# from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
# from matplotlib.figure import Figure
# from matplotlib import animation
#
# import cartopy
# import cartopy.crs as ccrs
# from cartopy.feature.nightshade import Nightshade
#
# class MapCanvas(FigureCanvas):
#     def __init__(self, parent=None):
#         self.current_time = datetime.now()
#
#         self.fig = Figure(dpi=48)
#         self.fig.subplots_adjust(left=0.04, right=.98, top=1, bottom=0.0)
#         self.fig.patch.set_alpha(0.0)
#         self.fig.patch.set_facecolor('none')
#
#         self.axes = self.fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
#         self.axes.set_extent([-179.9, 180, -90, 90])
#
#         params = {
#             'fig': self.fig,
#             'func': self.update_map,
#             'interval': 1,
#             'frames': self.frame_generator(),
#             'repeat': True,
#             'cache_frame_data': False,  # Disable frame data caching
#         }
#         self.anime = animation.FuncAnimation(**params)
#
#         super().__init__(self.fig)
#
#     def frame_generator(self):
#         while True:
#             yield datetime.now()
#
#     def update_map(self, frame):
#         self.current_time += timedelta(minutes=5)
#         self.axes.add_feature(cartopy.feature.LAND)
#         self.axes.add_feature(cartopy.feature.COASTLINE, lw=1)
#         self.axes.add_feature(cartopy.feature.RIVERS, lw=0.25)
#         self.axes.add_feature(cartopy.feature.LAKES)
#         self.axes.add_feature(cartopy.feature.BORDERS, linestyle='-', lw=0.5)
#         self.axes.add_feature(cartopy.feature.OCEAN)
#         self.axes.add_feature(Nightshade(self.current_time, alpha=0.2))
#         self.draw()
#
#
# class MainUi(QMainWindow):
#     def __init__(self):
#         super(MainUi, self).__init__()
#         self.resize(800, 450)
#
#         self.centralWidget = QWidget(self)
#         self.setCentralWidget(self.centralWidget)
#
#         # Create a QVBoxLayout instance
#         self.layout = QVBoxLayout()
#         self.map_canvas = MapCanvas()
#         self.layout.addWidget(self.map_canvas)
#
#         # Set the layout to the central widget
#         self.centralWidget.setLayout(self.layout)
#
#
# # unknown exception handler
# def handle_excepthook(type, message, stack):
#     print(f'An unhandled exception occured: {message}. Traceback: {traceback.format_tb(stack)}')
#
#
# async def main():
#     # manage unknown exceptions
#     sys.excepthook = handle_excepthook
#
#     app = QtWidgets.QApplication(sys.argv)
#     app.setQuitOnLastWindowClosed(True)
#     app.setAttribute(Qt.AA_EnableHighDpiScaling)  # Enable high DPI scaling
#     app.setAttribute(Qt.AA_UseHighDpiPixmaps)
#
#     loop = qasync.QEventLoop(app)
#     asyncio.set_event_loop(loop)
#
#     ui = MainUi()
#     QtWidgets.QApplication.processEvents()
#     ui.show()
#
#     with loop:
#         # Run the event loop until the UI is closed
#         await asyncio.gather(loop.create_task(loop.run_forever()),
#                              loop.run_in_executor(None, functools.partial(ui.show)))
#
#     # Cleanup and close the application
#     loop.close()
#     app.exec_()
#
#
# if __name__ == '__main__':
#     try:
#         asyncio.run(main())
#     except Exception as e:
#         print("An error occurred:", e)
#         traceback.print_exc()
#         sys.exit(1)