import sqlite3

# 1. Connect to the SQLite database (it will create the file if it doesn't exist)
conn = sqlite3.connect('products.db')
cursor = conn.cursor()

# 2. Create the 'products' table if it doesn't already exist
cursor.execute('''
    CREATE TABLE IF NOT EXISTS products (
        barcode TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        weight_grams INTEGER
    )
''')

# 3. Add some sample products to the database
# (This will only insert them if they don't already exist to avoid errors)
sample_products = [
    ('8906010500030', 'Balaji Waffers Masala Masti',10.00,30),
    ('8906010503529', 'Balaji Waffers Masala Masti',40.00,135),
    ('8906019561155','Real Bites sev Mamra',5.00,30),
    ('8901063033559','Britannia choco Star Cookie',120.00,275.6),
    ('8904077200016','Maniarrs Khakhara',66.00,200),

]

cursor.executemany('''
    INSERT OR IGNORE INTO products (barcode, name, price, weight_grams)
    VALUES (?, ?, ?, ?)
''', sample_products)

# Commit the changes (save them to the database file)
conn.commit()

print("Database 'products.db' and table 'products' are ready!")
#print("Sample products have been added.\n")

# 4. Function to find a product by its barcode
def find_product(barcode):
    cursor.execute("SELECT name, price FROM products WHERE barcode = ?", (barcode,))
    product = cursor.fetchone() # Fetch one matching result
    
    if product:
        name, price = product
        return name, price
    else:
        return None, None

# 5. Main program loop to simulate scanning
print("Smart Trolley System Started. Ready to scan...")
print("(For testing, type the barcode and press Enter.)")
print("Press 'Ctrl+C' to quit.\n")

try:
    while True:
        # Wait for input. When using a real scanner, this will be the barcode.
        scanned_data = input().strip()
        
        if scanned_data:
            name, price = find_product(scanned_data)
            if name:
                print(f"Product: {name} | Price: ${price:.2f}")
                # Here you would add it to the bill, update an LCD, etc.
            else:
                print(f"Product with barcode '{scanned_data}' not found!")
                # You could add code here to add a new product via an API.

except KeyboardInterrupt:
    print("\nShutting down. Goodbye!")

# 6. Close the database connection when the program ends
finally:
    conn.close()