"""Weather icons drawn on a Tk canvas: they scale to any screen, follow the text colour
(so they dim at night) and need no image files or extra packages."""


def draw(canvas, size, code, night, fg, bg):
    """Draw the icon for the OpenWeatherMap condition `code` in a size x size canvas."""
    canvas.delete("all")

    def circle(cx, cy, r, color):
        canvas.create_oval((cx - r) * size, (cy - r) * size, (cx + r) * size, (cy + r) * size,
                           fill=color, outline=color)

    def line(x0, y0, x1, y1, width=0.06):
        canvas.create_line(x0 * size, y0 * size, x1 * size, y1 * size, fill=fg,
                           width=max(1, round(size * width)), capstyle="round")

    def sun_or_moon(cx, cy, r):
        if night:                                            # crescent
            circle(cx, cy, r * 1.4, fg)
            circle(cx + r * 0.65, cy - r * 0.45, r * 1.15, bg)
            return
        circle(cx, cy, r, fg)
        for dx, dy in ((1, 0), (0.707, 0.707), (0, 1), (-0.707, 0.707),
                       (-1, 0), (-0.707, -0.707), (0, -1), (0.707, -0.707)):
            line(cx + dx * r * 1.45, cy + dy * r * 1.45, cx + dx * r * 1.85, cy + dy * r * 1.85)

    def cloud(dx=0.0, dy=0.0, scale=1.0, halo=0.0):
        """A cloud centred around (0.5, 0.5). `halo` leaves a gap around it."""
        def shape(grow, color):
            def x(v):
                return 0.5 + (v - 0.5) * scale + dx

            def y(v):
                return 0.5 + (v - 0.5) * scale + dy

            for cx, cy, r in ((0.28, 0.55, 0.17), (0.50, 0.42, 0.22), (0.72, 0.55, 0.17)):
                circle(x(cx), y(cy), r * scale + grow, color)
            canvas.create_rectangle(x(0.28) * size, y(0.50) * size,
                                    x(0.72) * size, (y(0.72) + grow) * size,
                                    fill=color, outline=color)

        if halo:
            shape(halo, bg)
        shape(0.0, fg)

    group = code // 100
    if group == 2:                                           # thunderstorm
        cloud(dy=-0.12)
        points = (0.52, 0.58, 0.38, 0.81, 0.49, 0.81, 0.43, 0.99, 0.66, 0.72, 0.54, 0.72, 0.62, 0.58)
        canvas.create_polygon([p * size for p in points], fill=fg, outline=bg)
    elif group == 3:                                         # drizzle
        cloud(dy=-0.12)
        for x in (0.32, 0.50, 0.68):
            line(x, 0.72, x - 0.02, 0.80)
    elif group == 5:                                         # rain
        cloud(dy=-0.12)
        for x in (0.32, 0.50, 0.68):
            line(x + 0.03, 0.70, x - 0.05, 0.92)
    elif group == 6:                                         # snow
        cloud(dy=-0.12)
        for x, y in ((0.28, 0.76), (0.50, 0.86), (0.72, 0.76)):
            for ax, ay in ((0.08, 0.0), (0.04, 0.069), (-0.04, 0.069)):
                line(x - ax, y - ay, x + ax, y + ay, 0.03)
    elif group == 7:                                         # mist, fog, haze
        for x0, x1, y in ((0.18, 0.78, 0.28), (0.28, 0.90, 0.43),
                          (0.10, 0.70, 0.58), (0.24, 0.84, 0.73)):
            line(x0, y, x1, y, 0.07)
    elif code == 800:                                        # clear sky
        sun_or_moon(0.5, 0.5, 0.25)
    elif code in (801, 802):                                 # few / scattered clouds
        sun_or_moon(0.66, 0.32, 0.15)
        cloud(dx=-0.08, dy=0.14, scale=0.85, halo=0.04)
    elif code == 804:                                        # overcast
        cloud(dx=0.17, dy=-0.14, scale=0.68)
        cloud(dx=-0.07, dy=0.10, scale=0.92, halo=0.04)
    else:                                                    # broken clouds
        cloud(dy=0.04, scale=1.1)
