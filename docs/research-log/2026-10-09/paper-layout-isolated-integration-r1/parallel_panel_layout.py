"""Retained coordinates and statistics; publication layout only."""
def parallel_panel_layout(plt, strong, weak, *, measured, reference):
    fig = plt.figure(figsize=(17/2.54, 9.3/2.54))
    ax = fig.add_axes((.095, .40, .425, .39))
    xs = [r['workers'] for r in strong]
    ax.errorbar(xs, [r['median'] for r in strong],
                yerr=[[r['median']-r['min'] for r in strong],
                      [r['max']-r['median'] for r in strong]],
                fmt='o-', color=measured, markerfacecolor='white',
                markersize=6.5, capsize=4, linewidth=1.5,
                label='Measured median', zorder=3)
    ax.plot(xs, xs, '--', color=reference, linewidth=1.1,
            label='Ideal speedup', zorder=2)
    ax.set(xlim=(.8,4.2), ylim=(0,4.3), xticks=xs, yticks=[0,1,2,3,4],
           xlabel='Independent-world workers', ylabel='Speedup')
    ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='y', color='#E7E9EC', linewidth=.4)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, pad=5)
    ax.xaxis.labelpad=7
    ax.yaxis.labelpad=7
    fig.text(.095,.95,'(a) Strong scaling',fontsize=14,ha='left')
    fig.text(.095,.89,'Four fixed worlds',fontsize=11.5,ha='left')
    fig.legend(*ax.get_legend_handles_labels(),loc='lower left',
               bbox_to_anchor=(.095,.81),frameon=False,fontsize=11.5,
               borderaxespad=0,ncol=2,columnspacing=1.0,
               handletextpad=.5,handlelength=1.5)
    # Numerical ranges remain readable even when their true plotted
    # spans are shorter than one typographic point. No interval is inflated.
    fig.text(.095,.225,'Workers',fontsize=11.5,ha='left')
    fig.text(.27,.225,'Median',fontsize=11.5,ha='right')
    fig.text(.52,.225,'Min-max',fontsize=11.5,ha='right')
    for y,r in zip((.16,.10,.04),strong):
        fig.text(.095,y,str(int(r['workers'])),fontsize=11.5,ha='left')
        fig.text(.27,y,f"{r['median']:.3f}",fontsize=11.5,ha='right')
        fig.text(.52,y,f"{r['min']:.3f}-{r['max']:.3f}",fontsize=11.5,ha='right')
    table = fig.add_axes((.595,.21,.395,.52))
    table.set_axis_off()
    fig.text(.595,.95,'(b) Weak scaling',fontsize=14,ha='left')
    fig.text(.595,.89,'Two worlds per worker',fontsize=11.5,ha='left')
    fig.text(.595,.81,'Efficiency (%)',fontsize=11.5,ha='left')
    table.text(0,.98,'Workers',ha='left',va='top',fontsize=11.5)
    table.text(.54,.98,'Median',ha='right',va='top',fontsize=11.5)
    table.text(1,.98,'Min-max',ha='right',va='top',fontsize=11.5)
    table.plot([0,1],[.83,.83],color='#9CA3AA',lw=.6)
    for y,r in zip((.65,.39,.13),weak):
        table.text(0,y,str(int(r['workers'])),ha='left',va='center',fontsize=12)
        table.text(.54,y,f"{100*r['median']:.2f}",ha='right',va='center',fontsize=12)
        table.text(1,y,f"{100*r['min']:.2f}-{100*r['max']:.2f}",ha='right',va='center',fontsize=12)
    table.plot([0,1],[0,0],color='#9CA3AA',lw=.6)
    table.set(xlim=(0,1),ylim=(0,1))
    fig.text(.595,.135,'Ideal efficiency: 100.00%',fontsize=11.5,ha='left')
    fig.text(.595,.07,'Ranges: three batches; not CIs',fontsize=11.5,ha='left')
    return fig
