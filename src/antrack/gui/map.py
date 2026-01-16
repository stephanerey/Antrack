import datetime
import numpy as np
import math

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import matplotlib.ticker as mticker
    from cartopy.mpl.ticker import LongitudeFormatter, LatitudeFormatter
    from cartopy.feature.nightshade import Nightshade
    import matplotlib.animation as animation
    cartopy_available = True
except ImportError:
    cartopy_available = False



class MapCanvas(FigureCanvas):
    def __init__(self, parent=None):
        fig = Figure(dpi=48)
        fig.subplots_adjust(left=0.04, right=.98, top=1, bottom=0.0)
        fig.patch.set_alpha(0.0)
        fig.patch.set_facecolor('none')

        if cartopy_available:
            self.axes = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
            self.axes.set_extent([-179.9, 180, -90, 90])

            dlon, dlat = 60, 30
            xticks = np.arange(-180, 180.1, dlon)
            yticks = np.arange(-90, 90.1, dlat)
            gl = self.axes.gridlines(
                crs=ccrs.PlateCarree(), draw_labels=False,
                linewidth=1, linestyle=':', color='k', alpha=0.8
            )
            gl.xlocator = mticker.FixedLocator(xticks)
            gl.ylocator = mticker.FixedLocator(yticks)

            self.axes.set_xticks(xticks, crs=ccrs.PlateCarree())
            self.axes.set_yticks(yticks, crs=ccrs.PlateCarree())
            self.axes.xaxis.set_major_formatter(LongitudeFormatter(zero_direction_label=True))
            self.axes.yaxis.set_major_formatter(LatitudeFormatter())
            for label in self.axes.get_yticklabels():
                label.set_fontsize(18)
                label.set_color('#CCE5FF')
            for label in self.axes.get_xticklabels():
                label.set_fontsize(18)
                label.set_color('#CCE5FF')

            self.current_nightshade = None
            self.anime = animation.FuncAnimation(fig, self.compute_initial_figure, interval=1000, frames=100, repeat=True)

        super().__init__(fig)

    def compute_initial_figure(self, frame):
        if cartopy_available:
            self.axes.coastlines()
            self.axes.add_feature(cfeature.LAND)
            self.axes.add_feature(cfeature.COASTLINE, lw=1)
            self.axes.add_feature(cfeature.RIVERS, lw=0.25)
            self.axes.add_feature(cfeature.LAKES)
            self.axes.add_feature(cfeature.BORDERS, linestyle='-', lw=0.5)
            self.axes.add_feature(cfeature.OCEAN)
            self.axes.add_feature(Nightshade(datetime.datetime.now(), alpha=0.2))

    def update_nightshade(self):
        if cartopy_available:
            if self.current_nightshade is not None:
                self.current_nightshade.remove()
            self.current_nightshade = Nightshade(datetime.datetime.now(), alpha=0.2)
            self.axes.add_feature(self.current_nightshade)
            self.axes.figure.canvas.draw_idle()

    def update_figure(self, lat, lon):
        if cartopy_available:
            self.axes.plot(lon, lat, 'ro', transform=ccrs.Geodetic())

    def calculate_endpoint(self, lat, lon, azimuth, length=1):
        azimuth_rad = math.radians(azimuth)
        delta_lon = length * math.sin(azimuth_rad)
        delta_lat = length * math.cos(azimuth_rad)
        return lon + delta_lon, lat + delta_lat

    def plot_direction_line(self, object_line, start_lat, start_lon, azimuth, length=100, color='r-'):
        if cartopy_available:
            end_lon, end_lat = self.calculate_endpoint(start_lat, start_lon, azimuth, length)
            if object_line is None:
                object_line, = self.axes.plot([start_lon, end_lon], [start_lat, end_lat], color, alpha=0.8, linewidth=3, transform=self.axes.projection)
            else:
                object_line.set_data([start_lon, end_lon], [start_lat, end_lat])
            self.axes.figure.canvas.draw_idle()
            return object_line