# smart_checkout_kiosk.py
# Enhanced Smart Checkout kiosk (PyQt5 + WebEngine + Flask + Razorpay)
# Modern UI with improved payment flow and additional features

import os
import sys
import json
import sqlite3
import threading
import time
import webbrowser
from io import BytesIO
from datetime import datetime
from enum import Enum

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Config (from env or edit here for quick testing)
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID") or ""
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET") or ""
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
SERIAL_PORT = os.getenv("SERIAL_PORT", "")  # e.g. /dev/ttyUSB0 or COM3 (optional)
SERIAL_BAUDRATE = int(os.getenv("SERIAL_BAUDRATE", "115200"))
STORE_NAME = os.getenv("STORE_NAME", "Smart Store")
STORE_UPI_ID = os.getenv("STORE_UPI_ID", "success@razorpay")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
IDLE_TIMEOUT = int(os.getenv("IDLE_TIMEOUT", "60"))  # seconds

FLASK_PORT = int(os.getenv("FLASK_PORT", "5001"))
DB_PATH = os.path.join(os.path.dirname(__file__), "kiosk_db.sqlite3")

# ---- Dependencies ----
try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
        QMessageBox, QInputDialog, QDialog, QStackedWidget, QFrame, 
        QScrollArea, QGridLayout, QComboBox, QSystemTrayIcon, QAction, QMenu,
        QToolButton, QSizePolicy, QSpacerItem, QProgressBar, QTextEdit
    )
    from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal, QEvent, QSize, QPoint, QPropertyAnimation, QEasingCurve
    from PyQt5.QtGui import QFont, QPixmap, QIcon, QColor, QPalette, QMovie, QPainter
    from PyQt5.QtWebEngineWidgets import QWebEngineView
except Exception as e:
    print("PyQt5 and PyQtWebEngine are required. Install via pip or apt. Error:", e)
    sys.exit(1)

try:
    import qrcode
except Exception:
    print("Install qrcode with: pip install qrcode[pil]")
    sys.exit(1)

try:
    import razorpay
except Exception:
    print("Install razorpay SDK: pip install razorpay")
    sys.exit(1)

try:
    import serial  # pyserial optional
    SERIAL_AVAILABLE = True
except Exception:
    SERIAL_AVAILABLE = False

from flask import Flask, request, render_template_string, jsonify

# Initialize Razorpay client
client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# ---- Flask app (runs in background thread) ----
flask_app = Flask(__name__)

# In-memory map to store last created order details (order_id -> order dict)
ORDER_CACHE = {}

# Simple Flask templates
CHECKOUT_PAGE = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Razorpay Checkout</title>
    <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
    <style>
      body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
        padding: 20px;
        background: #f8f9fa;
        color: #333;
        max-width: 500px;
        margin: 0 auto;
      }
      .container {
        background: white;
        border-radius: 12px;
        padding: 25px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
      }
      h3 {
        margin-top: 0;
        color: #2c3e50;
      }
      #pay {
        background: #5469d4;
        color: white;
        border: none;
        padding: 12px 24px;
        border-radius: 6px;
        font-size: 16px;
        font-weight: 600;
        cursor: pointer;
        width: 100%;
        transition: background 0.2s;
      }
      #pay:hover {
        background: #4457b4;
      }
      .amount {
        font-size: 24px;
        font-weight: bold;
        color: #27ae60;
        margin: 10px 0;
      }
    </style>
  </head>
  <body>
    <div class="container">
      <h3>Complete Payment</h3>
      <p>Order: <strong>{{ order_id }}</strong></p>
      <p>Amount: <span class="amount">₹{{ '%.2f'|format(amount/100) }}</span></p>
      <button id="pay">Pay Now</button>
    </div>
    <script>
      // Automatically trigger payment window on page load
      document.getElementById('pay').click();
      
      document.getElementById('pay').onclick = async function(){
        var options = {
          "key": "{{ key_id }}",
          "amount": {{ amount }},
          "currency": "INR",
          "name": "{{ store_name }}",
          "description": "Checkout Payment",
          "order_id": "{{ order_id }}",
          "handler": function (response){
            // POST verification to server
            var form = new FormData();
            form.append('razorpay_payment_id', response.razorpay_payment_id);
            form.append('razorpay_order_id', response.razorpay_order_id);
            form.append('razorpay_signature', response.razorpay_signature);
            fetch("/verify", {method:'POST', body: form})
              .then(r=>r.json())
              .then(j=>{
                if(j.status && j.status === 'ok'){
                  window.location = "/status/" + response.razorpay_payment_id;
                } else {
                  document.body.innerHTML += "<p style='color:red'>Verification failed: "+JSON.stringify(j)+"</p>";
                }
              })
              .catch(e=> { document.body.innerHTML += "<p style='color:red'>Error: "+e+"</p>"; });
          }
        };
        var rzp = new Razorpay(options);
        rzp.open();
      }
    </script>
  </body>
</html>
"""

STATUS_PAGE = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Payment Status</title>
    <style>
      body { font-family: Arial, sans-serif; padding: 20px; text-align: center; }
      .success { color: #28a745; font-size: 24px; font-weight: bold;}
      .failure { color: #dc3545; font-size: 24px; font-weight: bold;}
      .container { max-width: 400px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;}
    </style>
  </head>
  <body>
    <div class="container">
        <h3>Payment Status</h3>
        <p class="{{ 'success' if payment.status == 'captured' else 'failure' }}">
          Payment {{ 'Successful' if payment.status == 'captured' else 'Failed' }}
        </p>
        <p>ID: {{ payment.id }}</p>
        <p>Amount: ₹{{ "%.2f"|format(payment.amount/100) }}</p>
        <p>This window will close automatically.</p>
        <script>setTimeout(() => window.close(), 3000);</script>
    </div>
  </body>
</html>
"""

@flask_app.route('/checkout/<order_id>')
def checkout(order_id):
    order = ORDER_CACHE.get(order_id)
    if not order:
        return "Order not found", 404
    return render_template_string(CHECKOUT_PAGE, order_id=order_id, 
                                 amount=order['amount'], key_id=RAZORPAY_KEY_ID,
                                 store_name=STORE_NAME)

@flask_app.route('/verify', methods=['POST'])
def verify_payment():
    data = request.form.to_dict()
    required = ("razorpay_payment_id", "razorpay_order_id", "razorpay_signature")
    if not all(k in data for k in required):
        return jsonify({"error": "missing params"}), 400

    try:
        client.utility.verify_payment_signature(data)
        payment = client.payment.fetch(data["razorpay_payment_id"])

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO transactions (date, amount, status, razorpay_id, raw_json)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.utcnow().isoformat(), payment.get("amount"), payment.get("status"), payment.get("id"), json.dumps(payment)))
        conn.commit()
        conn.close()

        return jsonify({"status": "ok", "payment": payment})

    except Exception as e:
        print("Verification/DB error:", e)
        return jsonify({"error": "signature verification failed or DB error", "detail": str(e)}), 400


@flask_app.route('/status/<payment_id>')
def show_status(payment_id):
    try:
        payment = client.payment.fetch(payment_id)
    except Exception as e:
        return f"Could not fetch payment: {e}", 404
    return render_template_string(STATUS_PAGE, payment=payment)

def run_flask():
    flask_app.run(host='127.0.0.1', port=FLASK_PORT, debug=False, use_reloader=False)

# ---- Database init ----
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT UNIQUE, name TEXT, price REAL
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, amount INTEGER, status TEXT, razorpay_id TEXT, raw_json TEXT
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE, value TEXT
        )""")
    if cur.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        sample = [
            ('123456789012', 'Milk', 40.0), ('234567890123', 'Bread', 25.0),
            ('345678901234', 'Eggs', 60.0), ('456789012345', 'Butter', 55.0),
        ]
        cur.executemany("INSERT INTO products (barcode, name, price) VALUES (?, ?, ?)", sample)
    if cur.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
        settings = [
            ('store_name', STORE_NAME), ('upi_id', STORE_UPI_ID),
            ('razorpay_enabled', 'true'), ('upi_qr_enabled', 'true'),
            ('language', 'en'), ('theme', 'light')
        ]
        cur.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", settings)
    conn.commit()
    conn.close()

# ---- Helper Classes ----
class BarcodeEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.registerEventType())
    def __init__(self, barcode):
        super().__init__(self.EVENT_TYPE)
        self.barcode = barcode

class PaymentStatus(Enum):
    IDLE = 0
    PROCESSING = 1
    SUCCESS = 2
    FAILED = 3

class Theme:
    def __init__(self, name, background, foreground, accent, text, secondary):
        self.name, self.background, self.foreground, self.accent, self.text, self.secondary = name, background, foreground, accent, text, secondary

LIGHT_THEME = Theme("light", "#ffffff", "#f8f9fa", "#007bff", "#212529", "#6c757d")
DARK_THEME = Theme("dark", "#121212", "#1e1e1e", "#bb86fc", "#e0e0e0", "#a0a0a0")

# ---- Main PyQt GUI ----
class SmartKiosk(QMainWindow):
    payment_status_changed = pyqtSignal(PaymentStatus)
    theme_changed = pyqtSignal(Theme)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Checkout Kiosk")
        self.showFullScreen()
        self.settings = self.load_settings()
        self.current_theme = DARK_THEME if self.settings.get('theme', 'light') == 'dark' else LIGHT_THEME
        self.language = self.settings.get('language', 'en')
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        self.cart = []
        self.total = 0.0
        self.payment_status = PaymentStatus.IDLE
        self.last_activity = time.time()
        self.scanning_active = True
        self.payment_in_progress = False

        self.setup_ui()
        self.payment_status_changed.connect(self.update_payment_ui)
        self.theme_changed.connect(self.apply_theme)

        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self.check_idle)
        self.idle_timer.start(1000)

        self.focus_timer = QTimer(self)
        self.focus_timer.timeout.connect(self.ensure_hidden_focus)
        self.focus_timer.start(500)

        if SERIAL_AVAILABLE and SERIAL_PORT:
            threading.Thread(target=self.serial_scanner_thread, daemon=True).start()

    def setup_ui(self):
        self.central = QWidget()
        self.setCentralWidget(self.central)
        self.main_layout = QVBoxLayout(self.central)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.stacked_widget = QStackedWidget()
        self.main_layout.addWidget(self.stacked_widget)
        self.main_screen = QWidget()
        self.main_screen_layout = QHBoxLayout(self.main_screen)
        self.main_screen_layout.setContentsMargins(10, 10, 10, 10)
        self.main_screen_layout.setSpacing(15)

        # Left Panel
        self.left_panel = QFrame()
        self.left_panel.setObjectName("leftPanel")
        left_panel_layout = QVBoxLayout(self.left_panel)
        header = QHBoxLayout()
        self.store_label = QLabel(STORE_NAME)
        self.store_label.setObjectName("storeLabel")
        header.addWidget(self.store_label)
        header.addStretch()
        self.theme_btn = QToolButton()
        self.theme_btn.setObjectName("themeBtn")
        self.theme_btn.setText("🌙" if self.current_theme.name == "light" else "☀️")
        self.theme_btn.clicked.connect(self.toggle_theme)
        self.theme_btn.setFixedSize(40, 40)
        header.addWidget(self.theme_btn)
        left_panel_layout.addLayout(header)

        self.hidden_input = QLineEdit()
        self.hidden_input.returnPressed.connect(self.on_barcode_scanned)
        left_panel_layout.addWidget(self.hidden_input)

        # Manual input
        manual_input_layout = QHBoxLayout()
        self.manual_bar = QLineEdit()
        self.manual_bar.setPlaceholderText("Enter barcode manually")
        self.manual_bar.returnPressed.connect(self.manual_barcode_add)
        manual_input_layout.addWidget(self.manual_bar)
        
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["English", "Hindi"])
        self.lang_combo.setCurrentIndex(0 if self.language == 'en' else 1)
        self.lang_combo.currentIndexChanged.connect(self.change_language)
        manual_input_layout.addWidget(self.lang_combo)
        
        left_panel_layout.addLayout(manual_input_layout)

        self.cart_table = QTableWidget(0, 5)
        self.cart_table.setHorizontalHeaderLabels(["Product", "Price", "Qty", "Total", ""])
        for i, width in enumerate([QHeaderView.Stretch, QHeaderView.ResizeToContents, QHeaderView.ResizeToContents, QHeaderView.ResizeToContents, QHeaderView.ResizeToContents]):
            self.cart_table.horizontalHeader().setSectionResizeMode(i, width)
        self.cart_table.setSelectionBehavior(QTableWidget.SelectRows)
        left_panel_layout.addWidget(self.cart_table)

        cart_actions = QHBoxLayout()
        self.clear_btn = QPushButton("Clear Cart")
        self.clear_btn.clicked.connect(self.clear_cart)
        cart_actions.addWidget(self.clear_btn)
        cart_actions.addStretch()
        self.total_label = QLabel("Total: ₹0.00")
        self.total_label.setObjectName("totalLabel")
        cart_actions.addWidget(self.total_label)
        left_panel_layout.addLayout(cart_actions)

        # Right Panel
        self.right_panel = QFrame()
        self.right_panel.setObjectName("rightPanel")
        self.right_panel_layout = QVBoxLayout(self.right_panel)
        payment_title = QLabel("Payment Options")
        payment_title.setObjectName("paymentTitle")
        self.right_panel_layout.addWidget(payment_title)

        self.payment_status_widget = QWidget()
        payment_status_layout = QVBoxLayout(self.payment_status_widget)
        self.status_icon = QLabel()
        self.status_icon.setAlignment(Qt.AlignCenter)
        self.status_icon.setFixedSize(80, 80)
        payment_status_layout.addWidget(self.status_icon)
        self.status_label = QLabel("Ready for payment")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setObjectName("statusLabel")
        payment_status_layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        payment_status_layout.addWidget(self.progress_bar)
        self.right_panel_layout.addWidget(self.payment_status_widget)

        self.pay_btn = QPushButton("Checkout")
        self.pay_btn.setObjectName("payButton")
        self.pay_btn.clicked.connect(self.start_payment_flow)
        self.right_panel_layout.addWidget(self.pay_btn)
        
        self.right_panel_layout.addStretch()

        admin_buttons = QHBoxLayout()
        self.trans_btn = QPushButton("Transactions")
        self.trans_btn.clicked.connect(self.open_transactions)
        admin_buttons.addWidget(self.trans_btn)
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.open_settings)
        admin_buttons.addWidget(self.settings_btn)
        self.exit_btn = QPushButton("Exit")
        self.exit_btn.clicked.connect(self.admin_exit)
        admin_buttons.addWidget(self.exit_btn)
        self.right_panel_layout.addLayout(admin_buttons)
        
        # *** CRITICAL FIX: Add WebView to the layout ***
        self.webview = QWebEngineView()
        self.webview.setVisible(False)
        self.webview.urlChanged.connect(self.on_webview_url_changed)
        self.right_panel_layout.addWidget(self.webview, 1) # Add with stretch factor

        self.main_screen_layout.addWidget(self.left_panel, 2)
        self.main_screen_layout.addWidget(self.right_panel, 1)

        # Idle Screen
        self.idle_screen = QWidget()
        idle_layout = QVBoxLayout(self.idle_screen)
        idle_layout.setAlignment(Qt.AlignCenter)
        idle_label = QLabel(f"Welcome to {STORE_NAME}\nScan your first item to begin")
        idle_label.setAlignment(Qt.AlignCenter)
        idle_label.setObjectName("idleLabel")
        idle_layout.addWidget(idle_label)

        self.stacked_widget.addWidget(self.main_screen)
        self.stacked_widget.addWidget(self.idle_screen)
        self.apply_theme(self.current_theme)
        self.stacked_widget.setCurrentWidget(self.main_screen)

    def apply_theme(self, theme):
        self.current_theme = theme
        style = f"""
            QWidget {{ background: {theme.background}; color: {theme.text}; font-size: 14px; }}
            QFrame#leftPanel, QFrame#rightPanel {{ background: {theme.foreground}; border-radius: 10px; }}
            QLabel#storeLabel {{ font-size: 24px; font-weight: bold; color: {theme.accent}; }}
            QLabel#totalLabel {{ font-size: 18px; font-weight: bold; color: {theme.accent}; }}
            QLabel#paymentTitle {{ font-size: 20px; font-weight: bold; padding-bottom: 10px; border-bottom: 1px solid {theme.secondary}; }}
            QPushButton#payButton {{ background: {theme.accent}; color: white; border: none; padding: 15px; font-size: 16px; font-weight: bold; border-radius: 8px; }}
            QPushButton#payButton:disabled {{ background: {theme.secondary}; }}
            QLabel#statusLabel {{ font-size: 16px; font-weight: bold; }}
            QLabel#idleLabel {{ font-size: 32px; color: {theme.accent}; }}
            QTableWidget {{ gridline-color: {theme.secondary}; border: 1px solid {theme.secondary}; border-radius: 5px; background: {theme.background}; alternate-background-color: {theme.foreground}; }}
            QTableWidget::item {{ padding: 8px; border-bottom: 1px solid {theme.secondary}; }}
            QTableWidget::item:selected {{ background: {theme.accent}; color: white; }}
            QHeaderView::section {{ background: {theme.foreground}; color: {theme.text}; padding: 8px; border: none; border-bottom: 2px solid {theme.accent}; }}
            QLineEdit, QComboBox {{ padding: 10px; border: 1px solid {theme.secondary}; border-radius: 5px; background: {theme.background}; color: {theme.text}; }}
            QPushButton {{ padding: 10px; border: 1px solid {theme.secondary}; border-radius: 5px; background: {theme.foreground}; }}
            QPushButton:hover {{ background: {theme.accent}; color: white; }}
            QToolButton {{ border: 1px solid {theme.secondary}; border-radius: 20px; background: {theme.foreground}; }}
        """
        self.setStyleSheet(style)
        self.theme_btn.setText("☀️" if theme.name == "dark" else "🌙")

    def update_payment_ui(self, status):
        self.payment_status = status
        
        if status == PaymentStatus.IDLE:
            self.status_icon.clear()
            self.status_label.setText("Ready for payment")
            self.progress_bar.setVisible(False)
            self.pay_btn.setEnabled(len(self.cart) > 0)
        elif status == PaymentStatus.PROCESSING:
            self.status_label.setText("Processing payment...")
            self.progress_bar.setVisible(True)
            self.pay_btn.setEnabled(False)
        elif status == PaymentStatus.SUCCESS:
            self.status_icon.setText("✓")
            self.status_icon.setStyleSheet("font-size: 48px; color: #28a745;")
            self.status_label.setText("Payment Successful!")
            self.progress_bar.setVisible(False)
            self.pay_btn.setEnabled(False)
        elif status == PaymentStatus.FAILED:
            self.status_icon.setText("✗")
            self.status_icon.setStyleSheet("font-size: 48px; color: #dc3545;")
            self.status_label.setText("Payment Failed")
            self.progress_bar.setVisible(False)
            self.pay_btn.setEnabled(True)

    def toggle_theme(self):
        new_theme = DARK_THEME if self.current_theme.name == "light" else LIGHT_THEME
        self.theme_changed.emit(new_theme)
        self.save_setting('theme', new_theme.name)

    def change_language(self, index):
        self.language = 'en' if index == 0 else 'hi'
        self.save_setting('language', self.language)

    def load_settings(self):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        rows = cur.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}

    def save_setting(self, key, value):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        self.settings[key] = value

    def event(self, event):
        if isinstance(event, BarcodeEvent):
            self.add_barcode_to_cart(event.barcode)
            return True
        return super().event(event)

    def ensure_hidden_focus(self):
        if self.scanning_active and not self.webview.isVisible():
            self.hidden_input.setFocus()

    def on_barcode_scanned(self):
        self.record_activity()
        code = self.hidden_input.text().strip()
        self.hidden_input.clear()
        if code:
            self.add_barcode_to_cart(code)
            
    def manual_barcode_add(self):
        self.record_activity()
        code = self.manual_bar.text().strip()
        self.manual_bar.clear()
        if code:
            self.add_barcode_to_cart(code)

    def add_barcode_to_cart(self, barcode, qty=1):
        row = self.cur.execute("SELECT * FROM products WHERE barcode=?", (barcode,)).fetchone()
        if not row:
            QMessageBox.warning(self, "Product not found", f"No product for barcode: {barcode}")
            return
        
        for item in self.cart:
            if item["barcode"] == barcode:
                item["qty"] += qty
                self.refresh_cart_display()
                return

        self.cart.append({"barcode": barcode, "name": row["name"], "price": float(row["price"]), "qty": qty})
        self.refresh_cart_display()

    def refresh_cart_display(self):
        self.cart_table.setRowCount(len(self.cart))
        total = 0.0
        for row, item in enumerate(self.cart):
            self.cart_table.setItem(row, 0, QTableWidgetItem(item["name"]))
            price_item = QTableWidgetItem(f"₹{item['price']:.2f}")
            price_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.cart_table.setItem(row, 1, price_item)
            
            qty_widget = self.create_quantity_widget(row, item["qty"])
            self.cart_table.setCellWidget(row, 2, qty_widget)
            
            line_total = item["price"] * item["qty"]
            total_item = QTableWidgetItem(f"₹{line_total:.2f}")
            total_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.cart_table.setItem(row, 3, total_item)

            remove_btn = QPushButton("×")
            remove_btn.setFixedSize(25, 25)
            remove_btn.clicked.connect(lambda _, r=row: self.remove_item(r))
            self.cart_table.setCellWidget(row, 4, remove_btn)
            total += line_total
        
        self.total = total * 1.05 # Assuming 5% GST
        self.total_label.setText(f"Total: ₹{self.total:.2f} (incl. GST)")
        has_items = len(self.cart) > 0
        self.pay_btn.setEnabled(has_items)
        

    def create_quantity_widget(self, row, qty):
        qty_widget = QWidget()
        qty_layout = QHBoxLayout(qty_widget)
        qty_layout.setContentsMargins(0,0,0,0)
        qty_layout.setSpacing(0)
        dec_btn = QPushButton("-")
        dec_btn.setFixedSize(25, 25)
        dec_btn.clicked.connect(lambda _, r=row: self.change_quantity(r, -1))
        qty_label = QLabel(str(qty))
        qty_label.setAlignment(Qt.AlignCenter)
        qty_label.setFixedWidth(30)
        inc_btn = QPushButton("+")
        inc_btn.setFixedSize(25, 25)
        inc_btn.clicked.connect(lambda _, r=row: self.change_quantity(r, 1))
        for w in [dec_btn, qty_label, inc_btn]:
            qty_layout.addWidget(w)
        return qty_widget

    def change_quantity(self, row, delta):
        if 0 <= row < len(self.cart):
            self.cart[row]["qty"] += delta
            if self.cart[row]["qty"] <= 0:
                self.remove_item(row)
            else:
                self.refresh_cart_display()
    
    def remove_item(self, row):
        if 0 <= row < len(self.cart):
            del self.cart[row]
            self.refresh_cart_display()
    
    def clear_cart(self):
        self.cart = []
        self.refresh_cart_display()

    def start_payment_flow(self):
        if not self.cart: 
            QMessageBox.warning(self, "Empty cart", "Add items before payment.")
            return
            
        self.scanning_active, self.payment_in_progress = False, True
        self.hidden_input.clearFocus()
        self.payment_status_changed.emit(PaymentStatus.PROCESSING)
        
        try:
            order = client.order.create({
                "amount": int(round(self.total * 100)), 
                "currency": "INR",
                "receipt": f"rcpt_{int(time.time())}", 
                "payment_capture": 1
            })
            ORDER_CACHE[order['id']] = order
            url = f"http://127.0.0.1:{FLASK_PORT}/checkout/{order['id']}"
            self.webview.setUrl(QUrl(url))
            self.webview.setVisible(True) # Make it visible
            self.webview.setFocus()
        except Exception as e:
            QMessageBox.critical(self, "Order creation failed", f"Razorpay error: {e}")
            self.reset_payment_state(PaymentStatus.FAILED)

    def on_webview_url_changed(self, qurl):
        url = qurl.toString()
        if "/status/" in url:
            payment_id = url.rstrip('/').rsplit('/', 1)[-1] if '/' in url else None
            QTimer.singleShot(500, lambda: self.finish_payment_handling(payment_id))

    def finish_payment_handling(self, payment_id):
        self.webview.setVisible(False)
        status, payment = None, None
        if payment_id:
            try:
                payment = client.payment.fetch(payment_id)
                status = payment.get("status")
            except Exception as e:
                print("Error fetching payment:", e)
        
        if status == "captured":
            self.payment_status = PaymentStatus.SUCCESS
            self.show_receipt(payment)
            self.clear_cart()
        else:
            self.payment_status = PaymentStatus.FAILED
            QMessageBox.warning(self, "Payment Info", f"Payment status: {status or 'unknown'}.")
        
        self.reset_payment_state(self.payment_status)

    def reset_payment_state(self, final_status):
        self.scanning_active, self.payment_in_progress = True, False
        self.payment_status_changed.emit(final_status)
        self.hidden_input.setFocus()
        QTimer.singleShot(5000, self.reset_payment_status_to_idle)
    
    def reset_payment_status_to_idle(self):
        if self.payment_status != PaymentStatus.PROCESSING:
            self.payment_status_changed.emit(PaymentStatus.IDLE)
    
    def show_receipt(self, payment):
        dlg = QDialog(self)
        dlg.setWindowTitle("Payment Receipt")
        dlg.resize(400, 500)
        layout = QVBoxLayout(dlg)
        
        title = QLabel("Payment Successful")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #28a745;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        receipt_text = QTextEdit()
        receipt_text.setReadOnly(True)
        
        receipt_html = f"""
        <div style="font-family: Arial, sans-serif;">
            <h2 style="text-align: center; color: #28a745;">{STORE_NAME}</h2>
            <p style="text-align: center;">Payment Receipt</p>
            <hr>
            <table width="100%">
                <tr><td>Date:</td><td align="right">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
                <tr><td>Payment ID:</td><td align="right">{payment.get('id', 'N/A')}</td></tr>
                <tr><td>Amount:</td><td align="right">₹{payment.get('amount', 0)/100:.2f}</td></tr>
                <tr><td>Method:</td><td align="right">{payment.get('method', 'N/A')}</td></tr>
                <tr><td>Status:</td><td align="right">{payment.get('status', 'N/A')}</td></tr>
            </table>
            <hr>
            <h3>Items:</h3>
            <table width="100%">
        """
        
        for item in self.cart:
            receipt_html += f"""
                <tr>
                    <td>{item['name']} x {item['qty']}</td>
                    <td align="right">₹{item['price'] * item['qty']:.2f}</td>
                </tr>
            """
        
        receipt_html += f"""
            </table>
            <hr>
            <table width="100%">
                <tr><td>Subtotal:</td><td align="right">₹{self.total/1.05:.2f}</td></tr>
                <tr><td>GST (5%):</td><td align="right">₹{self.total*0.05:.2f}</td></tr>
                <tr><td><b>Total:</b></td><td align="right"><b>₹{self.total:.2f}</b></td></tr>
            </table>
            <hr>
            <p style="text-align: center;">Thank you for your purchase!</p>
        </div>
        """
        
        receipt_text.setHtml(receipt_html)
        layout.addWidget(receipt_text)
        
        button_layout = QHBoxLayout()
        print_btn = QPushButton("Print")
        # print_btn.clicked.connect(lambda: self.print_receipt(receipt_html))
        button_layout.addWidget(print_btn)
        
        email_btn = QPushButton("Email")
        # email_btn.clicked.connect(lambda: self.email_receipt(receipt_html))
        button_layout.addWidget(email_btn)
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
        dlg.exec_()

    def open_transactions(self):
        self.record_activity()
        rows = self.cur.execute("SELECT date, amount, status, razorpay_id FROM transactions ORDER BY id DESC LIMIT 100").fetchall()
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Transaction History")
        dlg.resize(600, 400)
        layout = QVBoxLayout(dlg)
        
        table = QTableWidget(len(rows), 4)
        table.setHorizontalHeaderLabels(["Date", "Amount", "Status", "Payment ID"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        
        for i, row in enumerate(rows):
            table.setItem(i, 0, QTableWidgetItem(row[0]))
            table.setItem(i, 1, QTableWidgetItem(f"₹{row[1]/100:.2f}"))
            table.setItem(i, 2, QTableWidgetItem(row[2]))
            table.setItem(i, 3, QTableWidgetItem(row[3]))
        
        layout.addWidget(table)
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        
        dlg.exec_()
    
    def show_qr_fallback(self):
        if not self.cart:
            QMessageBox.warning(self, "Empty cart", "Add items before generating QR.")
            return
        
        upi_uri = f"upi://pay?pa={STORE_UPI_ID}&pn={STORE_NAME}&am={self.total:.2f}&cu=INR"
        img = qrcode.make(upi_uri)
        buf = BytesIO()
        img.save(buf, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(buf.getvalue())
        
        dlg = QDialog(self)
        dlg.setWindowTitle("UPI QR Code")
        layout = QVBoxLayout(dlg)
        
        label = QLabel()
        label.setPixmap(pixmap.scaled(300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        
        amount_label = QLabel(f"Amount: ₹{self.total:.2f}")
        amount_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(amount_label)
        
        note_label = QLabel("Scan this QR code with your UPI app to pay")
        note_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(note_label)
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        
        dlg.exec_()

    def open_settings(self):
        self.record_activity()
        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        dlg.resize(400, 300)
        layout = QVBoxLayout(dlg)
        
        form_layout = QGridLayout()
        
        form_layout.addWidget(QLabel("Store Name:"), 0, 0)
        store_name_edit = QLineEdit(self.settings.get('store_name', STORE_NAME))
        form_layout.addWidget(store_name_edit, 0, 1)
        
        form_layout.addWidget(QLabel("UPI ID:"), 1, 0)
        upi_id_edit = QLineEdit(self.settings.get('upi_id', STORE_UPI_ID))
        form_layout.addWidget(upi_id_edit, 1, 1)
        
        form_layout.addWidget(QLabel("Razorpay Enabled:"), 2, 0)
        razorpay_check = QComboBox()
        razorpay_check.addItems(["Yes", "No"])
        razorpay_check.setCurrentIndex(0 if self.settings.get('razorpay_enabled', 'true') == 'true' else 1)
        form_layout.addWidget(razorpay_check, 2, 1)
        
        form_layout.addWidget(QLabel("UPI QR Enabled:"), 3, 0)
        upi_check = QComboBox()
        upi_check.addItems(["Yes", "No"])
        upi_check.setCurrentIndex(0 if self.settings.get('upi_qr_enabled', 'true') == 'true' else 1)
        form_layout.addWidget(upi_check, 3, 1)
        
        layout.addLayout(form_layout)
        
        button_layout = QHBoxLayout()
        save_btn = QPushButton("Save")
        
        def save_settings():
            self.save_setting('store_name', store_name_edit.text())
            self.save_setting('upi_id', upi_id_edit.text())
            self.save_setting('razorpay_enabled', 'true' if razorpay_check.currentIndex() == 0 else 'false')
            self.save_setting('upi_qr_enabled', 'true' if upi_check.currentIndex() == 0 else 'false')
            
            global STORE_NAME, STORE_UPI_ID
            STORE_NAME = store_name_edit.text()
            STORE_UPI_ID = upi_id_edit.text()
            self.store_label.setText(STORE_NAME)
            
            dlg.accept()
        
        save_btn.clicked.connect(save_settings)
        button_layout.addWidget(save_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
        dlg.exec_()

    def admin_exit(self):
        password, ok = QInputDialog.getText(self, "Admin Exit", "Enter admin password:", QLineEdit.Password)
        if ok and password == ADMIN_PASSWORD:
            self.close()

    def record_activity(self):
        self.last_activity = time.time()
        if self.stacked_widget.currentWidget() is self.idle_screen:
            self.stacked_widget.setCurrentWidget(self.main_screen)

    def check_idle(self):
        if time.time() - self.last_activity > IDLE_TIMEOUT and not self.payment_in_progress:
            self.clear_cart()
            self.stacked_widget.setCurrentWidget(self.idle_screen)
            
    def serial_scanner_thread(self):
        try:
            ser = serial.Serial(SERIAL_PORT, SERIAL_BAUDRATE, timeout=1)
            print("Serial scanner listening on", SERIAL_PORT)
            buf = ""
            while True:
                ch = ser.read().decode(errors='ignore')
                if not ch:
                    continue
                if ch in ("\n", "\r"):
                    barcode = buf.strip()
                    buf = ""
                    if barcode:
                        QApplication.postEvent(self, BarcodeEvent(barcode))
                else:
                    buf += ch
        except Exception as e:
            print("Serial scanner error:", e)
    
    def closeEvent(self, event):
        self.conn.close()
        print("Database connection closed. Exiting.")
        event.accept()

# ---- Main Execution ----
def main():
    init_db()
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    time.sleep(0.5)
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    kiosk = SmartKiosk()
    kiosk.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
