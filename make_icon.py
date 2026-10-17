import sys
from PySide6.QtWidgets import QApplication
import orbi  # Imports your orbi.py file

app = QApplication(sys.argv)

# Generate the Orbi face at 256x256 (Windows standard for high-res icons)
icon = orbi.make_tray_icon(theme_name="classic", size=256)

# Save it as a .ico file
icon.pixmap(256, 256).save("orbi.ico", "ICO")
print("Created orbi.ico successfully!")