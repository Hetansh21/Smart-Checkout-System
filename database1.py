# smart_trolley.py
import csv

def load_products_from_csv(filename='products.csv'):
#load data in product variable
    products = {}
    try:
        with open(filename, mode='r', newline='') as file:
            reader = csv.DictReader(file)
            for row in reader:
                # Use barcode as the key for easy lookup
                products[row['barcode']] = {
                    'name': row['name'],
                    'price': float(row['price']),
                    'weight_grams': float(row['weight_grams'])
                }
        print(f"✅ Loaded {len(products)} products from {filename}")
        return products
    except FileNotFoundError:
        print(f"❌ Error: File {filename} not found. Please create it first.")
        return {}

def find_product(products_dict, barcode):
    """Find a product by barcode in the dictionary"""
    return products_dict.get(barcode)

def main():
    # Load all products from CSV into memory
    products = load_products_from_csv()
    
    if not products:
        return  # Exit if no products loaded
    
    print("🛒 Smart Trolley System Started (CSV Version)")
    print("📟 Ready to scan products...")
    print("⏹️  Press 'Ctrl+C' to quit\n")
    
    try:
        while True:
            # Wait for barcode input
            scanned_barcode = input().strip()
            
            if scanned_barcode:
                product = find_product(products, scanned_barcode)
                if product:
                    print(f"✅ Product: {product['name']}")
                    print(f"   Price: ₹{product['price']:.2f}")
                    print(f"   Weight: {product['weight_grams']}g")
                    print("---")
                else:
                    print(f"❌ Product not found! Barcode: {scanned_barcode}")
                    print("---")
                    
    except KeyboardInterrupt:
        print("\n🛑 System stopped. Goodbye!")

if __name__ == "__main__":
    main()