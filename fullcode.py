# smart_checkout_kiosk.py
# Single-file Smart Checkout kiosk (PyQt5 + WebEngine + Flask + Razorpay)
# Save as smart_checkout_kiosk.py

import os
import sys
import json
import sqlite3
import threading
import time
from io import BytesIO
from datetime import datetime

# load .env if available
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

FLASK_PORT = int(os.getenv("FLASK_PORT", "5001"))
DB_PATH = os.path.join(os.path.dirname(__file__), "kiosk_db.sqlite3")

# ---- dependencies that must be installed ----
try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
        QMessageBox, QInputDialog
    )
    from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal, QEvent
    from PyQt5.QtGui import QFont, QPixmap
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

# initialize Razorpay client
client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# ---- Flask app (runs in background thread) ----
flask_app = Flask(__name__)

# In-memory map to store last created order details (order_id -> order dict)
# also persisted to DB below
ORDER_CACHE = {}

# Simple Flask templates
CHECKOUT_PAGE = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>Razorpay Checkout</title>
    <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
    <style>body{font-family:Arial, Helvetica, sans-serif; padding:20px;}</style>
  </head>
  <body>
    <h3>Complete Payment</h3>
    <p>Order: <strong>{{ order_id }}</strong></p>
    <p>Amount: <strong>₹{{ '%.2f'|format(amount/100) }}</strong></p>
    <button id="pay">Open Razorpay Checkout</button>
    <script>
      document.getElementById('pay').onclick = async function(){
        var options = {
          "key": "{{ key_id }}",
          "amount": {{ amount }},
          "currency": "INR",
          "name": "Smart Checkout",
          "description": "Smart Checkout Payment",
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
  <head><meta charset="utf-8"><title>Payment Status</title></head>
  <body>
    <h3>Payment Status</h3>
    <pre>{{ payment | tojson(indent=2) }}</pre>
    <p><a href="#" onclick="window.close()">Close</a></p>
  </body>
</html>
"""

@flask_app.route('/checkout/<order_id>')
def checkout(order_id):
    order = ORDER_CACHE.get(order_id)
    if not order:
        return "Order not found", 404
    return render_template_string(CHECKOUT_PAGE, order_id=order_id, amount=order['amount'], key_id=RAZORPAY_KEY_ID)

@flask_app.route('/verify', methods=['POST'])
def verify_payment():
    data = request.form.to_dict()
    required = ("razorpay_payment_id", "razorpay_order_id", "razorpay_signature")
    if not all(k in data for k in required):
        return jsonify({"error": "missing params"}), 400

    payload = {
        "razorpay_payment_id": data["razorpay_payment_id"],
        "razorpay_order_id": data["razorpay_order_id"],
        "razorpay_signature": data["razorpay_signature"]
    }
    try:
        client.utility.verify_payment_signature(payload)
    except Exception as e:
        return jsonify({"error": "signature verification failed", "detail": str(e)}), 400

    # fetch payment details
    try:
        payment = client.payment.fetch(payload["razorpay_payment_id"])
    except Exception as e:
        return jsonify({"error": "fetch failed", "detail": str(e)}), 500

    # persist payment to DB
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO transactions (date, amount, status, razorpay_id, raw_json)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.utcnow().isoformat(), payment.get("amount"), payment.get("status"), payment.get("id"), json.dumps(payment)))
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB persist error:", e)

    return jsonify({"status": "ok", "payment": payment})

@flask_app.route('/status/<payment_id>')
def show_status(payment_id):
    try:
        payment = client.payment.fetch(payment_id)
    except Exception as e:
        return f"Could not fetch payment: {e}", 404
    return render_template_string(STATUS_PAGE, payment=payment)


def run_flask():
    # Run Flask server for embedded checkout
    flask_app.run(host='127.0.0.1', port=FLASK_PORT, debug=False, use_reloader=False)


# ---- Database init ----
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT UNIQUE,
            name TEXT,
            price REAL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            amount INTEGER,
            status TEXT,
            razorpay_id TEXT,
            raw_json TEXT
        )
    """)
    # sample products
    cur.execute("SELECT COUNT(*) FROM products")
    if cur.fetchone()[0] == 0:
        sample = [
            ('123456789012','Milk',40.0),
            ('234567890123','Bread',25.0),
            ('345678901234','Eggs',60.0),
            ('456789012345','Butter',55.0),
        ]
        cur.executemany("INSERT INTO products (barcode,name,price) VALUES (?,?,?)", sample)
    conn.commit()
    conn.close()

# Custom event for cross-thread communication
class BarcodeEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.registerEventType())
    
    def __init__(self, barcode):
        super().__init__(self.EVENT_TYPE)
        self.barcode = barcode

# ---- PyQt GUI ----
class SmartKiosk(QMainWindow):
    barcode_scanned = pyqtSignal(str)  # Signal for cross-thread barcode events
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Checkout Kiosk")
        self.showFullScreen()
        self.setCursor(Qt.BlankCursor)  # optional: hide cursor in kiosk mode

        # DB
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()

        self.cart = []  # list of dicts: {barcode,name,price,qty}
        self.total = 0.0

        # central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout()
        central.setLayout(main_layout)

        # Left: scanner + cart
        left = QVBoxLayout()
        title = QLabel("Smart Checkout")
        title.setFont(QFont("Arial", 28, QFont.Bold))
        left.addWidget(title)

        # Barcode input (hidden but focused) - captures keyboard-emulating scanners
        self.hidden_input = QLineEdit()
        self.hidden_input.returnPressed.connect(self.on_barcode_scanned)
        self.hidden_input.setPlaceholderText("Scanner input (auto-focused)")
        left.addWidget(self.hidden_input)
        self.hidden_input.setFocus()

        scan_label = QLabel("OR Enter Barcode Manually:")
        left.addWidget(scan_label)
        self.manual_bar = QLineEdit()
        self.manual_bar.setPlaceholderText("Enter barcode here and press Enter")
        self.manual_bar.returnPressed.connect(self.manual_barcode_add)
        left.addWidget(self.manual_bar)

        # cart table
        self.table = QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(["Name","Price","Qty","Total"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        left.addWidget(self.table)

        btns = QHBoxLayout()
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self.remove_selected)
        clear_btn = QPushButton("Clear Cart")
        clear_btn.clicked.connect(self.clear_cart)
        btns.addWidget(remove_btn)
        btns.addWidget(clear_btn)
        left.addLayout(btns)

        main_layout.addLayout(left, 2)

        # Right: totals + payment
        right = QVBoxLayout()
        self.lbl_total = QLabel("Total: ₹0.00")
        self.lbl_total.setFont(QFont("Arial", 20, QFont.Bold))
        right.addWidget(self.lbl_total)

        pay_btn = QPushButton("Pay with Razorpay (Embedded)")
        pay_btn.setStyleSheet("font-size:16px; padding:10px")
        pay_btn.clicked.connect(self.start_payment_flow)
        right.addWidget(pay_btn)

        qr_btn = QPushButton("Show UPI QR (Fallback)")
        qr_btn.clicked.connect(self.show_qr_fallback)
        right.addWidget(qr_btn)

        trans_btn = QPushButton("Transactions")
        trans_btn.clicked.connect(self.open_transactions)
        right.addWidget(trans_btn)

        exit_btn = QPushButton("Admin Exit")
        exit_btn.clicked.connect(self.admin_exit)
        right.addWidget(exit_btn)

        # Embedded webview for checkout pages (hidden until use)
        self.webview = QWebEngineView()
        self.webview.setVisible(False)
        right.addWidget(self.webview, 3)

        main_layout.addLayout(right, 1)

        # --- NEW: scanning control flags ---
        self.scanning_active = True        # when True, we keep scanner input focused
        self.payment_in_progress = False   # True while user is in checkout

        # Ensure webview navigation is observed so we can re-enable scanning after payment
        self.webview.urlChanged.connect(self.on_webview_url_changed)
        
        # Connect barcode signal
        self.barcode_scanned.connect(self.add_barcode_to_cart)

        # Timer to keep focus on hidden input (so scanner input goes there)
        self.focus_timer = QTimer()
        self.focus_timer.timeout.connect(self.ensure_hidden_focus)
        self.focus_timer.start(500)

        # Optionally start serial scanner thread
        if SERIAL_AVAILABLE and SERIAL_PORT:
            threading.Thread(target=self.serial_scanner_thread, daemon=True).start()

    def event(self, event):
        """Handle custom events from other threads"""
        if isinstance(event, BarcodeEvent):
            self.add_barcode_to_cart(event.barcode)
            return True
        return super().event(event)

    # ---- scanner handling ----
    def ensure_hidden_focus(self):
        # Only force-focus the hidden scanner input when scanning is active
        # and the webview is not visible (checkout not in progress).
        if not self.scanning_active:
            return
        # if webview is visible, don't steal focus
        if self.webview.isVisible():
            return
        if not self.hidden_input.hasFocus():
            self.hidden_input.setFocus()

    def on_barcode_scanned(self):
        code = self.hidden_input.text().strip()
        self.hidden_input.clear()
        if code:
            self.add_barcode_to_cart(code)

    def manual_barcode_add(self):
        code = self.manual_bar.text().strip()
        self.manual_bar.clear()
        if code:
            self.add_barcode_to_cart(code)

    def add_barcode_to_cart(self, barcode, qty=1):
        # lookup product
        self.cur.execute("SELECT * FROM products WHERE barcode=?", (barcode,))
        row = self.cur.fetchone()
        if not row:
            QMessageBox.warning(self, "Product not found", f"No product for barcode: {barcode}")
            return False
        name = row["name"]
        price = float(row["price"])
        # check if in cart
        for it in self.cart:
            if it["barcode"] == barcode:
                it["qty"] += qty
                self.refresh_cart_display()
                return True
        self.cart.append({"barcode": barcode, "name": name, "price": price, "qty": qty})
        self.refresh_cart_display()
        return True

    def refresh_cart_display(self):
        self.table.setRowCount(len(self.cart))
        total = 0.0
        for r, it in enumerate(self.cart):
            self.table.setItem(r, 0, QTableWidgetItem(it["name"]))
            self.table.setItem(r, 1, QTableWidgetItem(f"₹{it['price']:.2f}"))
            self.table.setItem(r, 2, QTableWidgetItem(str(it["qty"])))
            line_total = it["price"] * it["qty"]
            self.table.setItem(r, 3, QTableWidgetItem(f"₹{line_total:.2f}"))
            total += line_total
        gst = total * 0.05
        total_with_tax = total + gst
        self.total = total_with_tax
        self.lbl_total.setText(f"Total: ₹{total_with_tax:.2f}")

    def remove_selected(self):
        r = self.table.currentRow()
        if r >=0 and r < len(self.cart):
            del self.cart[r]
            self.refresh_cart_display()

    def clear_cart(self):
        self.cart = []
        self.refresh_cart_display()

    # ---- serial scanner thread (optional) ----
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
                        # Post custom event to main thread
                        QApplication.postEvent(self, BarcodeEvent(barcode))
                else:
                    buf += ch
        except Exception as e:
            print("Serial scanner error:", e)

    # ---- payment flow ----
    def start_payment_flow(self):
        if not self.cart:
            QMessageBox.warning(self, "Empty cart", "Add items before payment.")
            return

        # Disable scanner auto-focus while the user uses checkout
        self.scanning_active = False
        self.payment_in_progress = True
        try:
            self.hidden_input.clearFocus()
        except Exception:
            pass

        # Create razorpay order
        amount_paise = int(round(self.total * 100))
        receipt = f"rcpt_{os.urandom(6).hex()}"
        try:
            order = client.order.create({
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "payment_capture": 1
            })
        except Exception as e:
            QMessageBox.critical(self, "Order creation failed", f"Razorpay error: {e}")
            # Re-enable scanning if order creation fails
            self.scanning_active = True
            self.payment_in_progress = False
            return

        ORDER_CACHE[order['id']] = order

        # open checkout page from embedded Flask server and give focus to webview
        url = f"http://127.0.0.1:{FLASK_PORT}/checkout/{order['id']}"
        self.webview.setVisible(True)
        self.webview.setUrl(QUrl(url))
        # ensure webview receives keyboard input
        self.webview.setFocus()

    def on_webview_url_changed(self, qurl):
        """
        Called when embedded webview navigates somewhere.
        We detect navigation to /status/<payment_id> as an indication
        that the payment flow completed and verification happened.
        """
        url = qurl.toString()
        # If the webview points to the status page, fetch status and finish
        if "/status/" in url:
            # extract payment_id from URL (last path segment)
            try:
                payment_id = url.rstrip('/').rsplit('/', 1)[-1]
            except Exception:
                payment_id = None

            # call finish handler after a short delay (allow page to render)
            QTimer.singleShot(600, lambda: self.finish_payment_handling(payment_id))

    def finish_payment_handling(self, payment_id):
        """
        Fetch payment status directly via razorpay SDK, hide webview,
        re-enable scanner and update UI / store result accordingly.
        """
        status = None
        try:
            if payment_id:
                payment = client.payment.fetch(payment_id)
                status = payment.get("status")
            else:
                payment = None
        except Exception as e:
            payment = None
            status = None
            print("Error fetching payment:", e)

        # hide webview and re-enable scanner
        self.webview.setVisible(False)
        self.scanning_active = True
        self.payment_in_progress = False

        # If captured -> show success & clear cart; else show message but keep cart intact
        if status == "captured":
            QMessageBox.information(self, "Payment Successful", f"Payment captured.\nID: {payment_id}")
            # clear cart & update UI
            self.clear_cart()
            self.refresh_cart_display()
        else:
            # if we have a payment object, show its status; otherwise generic message
            msg = f"Payment status: {status}" if status else "Payment completed (status unknown)."
            QMessageBox.warning(self, "Payment Info", msg)
            # do not clear cart in case of failed/pending so cashier can retry or handle manually
            self.refresh_cart_display()

        # return focus to scanner input (ensure scanner ready)
        QTimer.singleShot(200, lambda: self.hidden_input.setFocus() if self.scanning_active else None)

    def show_qr_fallback(self):
        # Generate simple UPI QR using qrcode
        upi_uri = f"upi://pay?pa=success@razorpay&pn=DemoMerchant&am={self.total:.2f}&cu=INR"
        img = qrcode.make(upi_uri)
        buf = BytesIO()
        img.save(buf, format="PNG")
        pix = QPixmap()
        pix.loadFromData(buf.getvalue())
        dlg = QMessageBox(self)
        dlg.setWindowTitle("UPI QR (Fallback)")
        lbl = QLabel()
        lbl.setPixmap(pix.scaled(280,280, Qt.KeepAspectRatio))
        dlg.layout().addWidget(lbl, 0, 1)
        dlg.exec_()

    def open_transactions(self):
        # open a simple window listing transactions from DB
        rows = self.cur.execute("SELECT date, amount, status, razorpay_id FROM transactions ORDER BY id DESC LIMIT 200").fetchall()
        s = "\n".join([f"{r[0]} | ₹{r[1]/100:.2f} | {r[2]} | {r[3]}" for r in rows])
        QMessageBox.information(self, "Transactions", s if s else "No transactions yet")

    def admin_exit(self):
        pwd, ok = QInputDialog.getText(self, "Admin", "Enter admin password:", QLineEdit.Password)
        if not ok:
            return
        if pwd == "admin123":  # change for production
            self.conn.close()
            QApplication.quit()
        else:
            QMessageBox.warning(self, "Denied", "Wrong password")

# ---- main ----
def main():
    init_db()
    # start flask in background thread
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    time.sleep(0.5)  # small wait for server to start

    app = QApplication(sys.argv)
    kiosk = SmartKiosk()
    kiosk.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
