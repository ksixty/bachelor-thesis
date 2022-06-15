import matplotlib
import matplotlib.ticker as mtick
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib import rc
import random

random.seed('foo')
#matplotlib.use("pgf")
matplotlib.use("GTK4Agg")
cmap = mcolors.LinearSegmentedColormap.from_list("", [(0, "red"), (0.89, "red"), (0.998, "orange"), (1, "green")])

def m(minutes): return minutes * 60
def h(hours): return m(hours * 60)

def s2m(seconds): return seconds / 60

times = {
    '2018 Quals':  (m(50), h(8)),
    '2018 Finals': (m(30), h(6)),
    '2019 Quals':  (m(103), h(8)),
    '2019 Finals': (m(33), h(8)),
    '2020 Quals':  (m(252), h(36)),
    '2020 School': (m(10), h(5)),
    '2021 Quals':  (m(46), h(36)),
    '2021 School': (m(24), h(5)),
    '2022 Quals':  (m(1), h(72)),
    '2022 School': (m(3), h(5)),
}

rel_times = [1 - (down / total) for down, total in times.values()]
rel_colors = [t / max(rel_times) for t in rel_times]
rel_mean = sum(rel_times) / len(rel_times)
down = [s2m(down) for down, _ in times.values()]
total = [s2m(total) for _, total in times.values()]

fig, ax = plt.subplots(figsize=(6,4))

ax.barh(list(times.keys()), rel_times, color=cmap(rel_colors))
ax.set_ylabel("Соревнование Ugra CTF")
ax.set_xlabel("Доля времени, когда все ресурсы были доступны")
ax.xaxis.set_major_formatter(mtick.PercentFormatter(xmax=1, decimals=0))
ax.xaxis.set_ticks([x / 100 for x in range(0, 100 + 1, 5)])
ax.set_xlim(0.7, 1.005)
# average
avg_color = cmap(rel_mean)
avg_down = sum(down) / len(times)
avg_total = int(sum(total) / len(times))
ax.axvline(rel_mean, color=avg_color, linewidth=1, linestyle=':')
ax.annotate('Среднее ≈ {:0.2f}%'.format(rel_mean*100, avg_total - avg_down, avg_total),
            xy=(rel_mean, 1), xytext=(-20, 10), color=avg_color,
            xycoords=('data', 'axes fraction'), textcoords='offset points',
            horizontalalignment='right', verticalalignment='center',
            arrowprops=dict(arrowstyle='-|>', color=avg_color, fc=avg_color, shrinkA=0, shrinkB=0,
                            connectionstyle='angle,angleA=0,angleB=90,rad=10'),
            )

fig.savefig('/home/k60/thesis/graphics/svg/downtimes-after.pdf', backend="pgf", bbox_inches='tight')
