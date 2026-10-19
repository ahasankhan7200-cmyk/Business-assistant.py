import sqlite3
from datetime import date

import pandas as pd
import streamlit as st

DB_PATH = "inventory.db"

st.set_page_config(page_title="Inventory Manager", page_icon="📦", layout="wide")


# ---------------------------------------------------------------- database
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                sku           TEXT UNIQUE NOT NULL,
                name          TEXT NOT NULL,
                category      TEXT,
                cost_price    REAL NOT NULL DEFAULT 0,
                sell_price    REAL NOT NULL DEFAULT 0,
                quantity      INTEGER NOT NULL DEFAULT 0,
                reorder_level INTEGER NOT NULL DEFAULT 5
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_date    TEXT NOT NULL,
                product_id INTEGER NOT NULL REFERENCES products(id),
                tx_type    TEXT NOT NULL CHECK (tx_type IN ('Sale', 'Purchase')),
                quantity   INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                party      TEXT,
                note       TEXT
            );
            """
        )


def query(sql, params=()):
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def execute(sql, params=()):
    with get_conn() as conn:
        conn.execute(sql, params)


def get_products():
    return query("SELECT * FROM products ORDER BY name")


def record_transaction(tx_date, product_id, tx_type, qty, price, party, note):
    """Insert a transaction and update stock in one atomic step."""
    delta = qty if tx_type == "Purchase" else -qty
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO transactions
               (tx_date, product_id, tx_type, quantity, unit_price, party, note)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(tx_date), product_id, tx_type, qty, price, party, note),
        )
        conn.execute(
            "UPDATE products SET quantity = quantity + ? WHERE id = ?",
            (delta, product_id),
        )


def delete_transaction(tx_id):
    """Delete a transaction and reverse its effect on stock."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT product_id, tx_type, quantity FROM transactions WHERE id = ?",
            (tx_id,),
        ).fetchone()
        if row is None:
            return
        reverse = -row["quantity"] if row["tx_type"] == "Purchase" else row["quantity"]
        conn.execute(
            "UPDATE products SET quantity = quantity + ? WHERE id = ?",
            (reverse, row["product_id"]),
        )
        conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


# ---------------------------------------------------------------- optional login
def check_password():
    """Password is read from st.secrets['password']. If not set, login is skipped."""
    try:
        correct = st.secrets["password"]
    except Exception:
        return True
    if st.session_state.get("authed"):
        return True
    pwd = st.text_input("Password", type="password")
    if st.button("Log in"):
        if pwd == correct:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Wrong password")
    return False


# ---------------------------------------------------------------- pages
def page_dashboard():
    st.title("📊 Dashboard")
    products = get_products()
    tx = query(
        """SELECT t.*, p.name FROM transactions t
           JOIN products p ON p.id = t.product_id"""
    )

    stock_value = (products["quantity"] * products["cost_price"]).sum() if not products.empty else 0
    sales = tx.loc[tx.tx_type == "Sale"].eval("quantity * unit_price").sum() if not tx.empty else 0
    purchases = tx.loc[tx.tx_type == "Purchase"].eval("quantity * unit_price").sum() if not tx.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Products", len(products))
    c2.metric("Stock value (at cost)", f"{stock_value:,.2f}")
    c3.metric("Total sales", f"{sales:,.2f}")
    c4.metric("Total purchases", f"{purchases:,.2f}")

    st.subheader("⚠️ Low stock")
    low = products[products["quantity"] <= products["reorder_level"]]
    if low.empty:
        st.success("All products are above their reorder level.")
    else:
        st.dataframe(
            low[["sku", "name", "quantity", "reorder_level"]],
            use_container_width=True, hide_index=True,
        )

    st.subheader("Monthly sales vs purchases")
    if tx.empty:
        st.info("No transactions yet.")
    else:
        tx["month"] = pd.to_datetime(tx["tx_date"]).dt.to_period("M").astype(str)
        tx["amount"] = tx["quantity"] * tx["unit_price"]
        chart = tx.pivot_table(index="month", columns="tx_type", values="amount",
                               aggfunc="sum", fill_value=0)
        st.bar_chart(chart)


def page_products():
    st.title("📦 Products")
    tab_list, tab_add, tab_edit = st.tabs(["All products", "Add product", "Edit / delete"])

    with tab_list:
        products = get_products()
        search = st.text_input("Search by name, SKU or category")
        if search:
            mask = products.apply(
                lambda r: search.lower() in f"{r['name']} {r['sku']} {r['category']}".lower(),
                axis=1,
            )
            products = products[mask]
        st.dataframe(products.drop(columns="id"), use_container_width=True, hide_index=True)
        st.download_button("Download CSV", products.to_csv(index=False),
                           "products.csv", "text/csv")

    with tab_add:
        with st.form("add_product", clear_on_submit=True):
            c1, c2 = st.columns(2)
            sku = c1.text_input("SKU *")
            name = c2.text_input("Name *")
            category = c1.text_input("Category")
            qty = c2.number_input("Opening quantity", min_value=0, step=1)
            cost = c1.number_input("Cost price", min_value=0.0, step=0.5)
            sell = c2.number_input("Selling price", min_value=0.0, step=0.5)
            reorder = c1.number_input("Reorder level", min_value=0, value=5, step=1)
            if st.form_submit_button("Add product"):
                if not sku.strip() or not name.strip():
                    st.error("SKU and name are required.")
                else:
                    try:
                        execute(
                            """INSERT INTO products
                               (sku, name, category, cost_price, sell_price, quantity, reorder_level)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (sku.strip(), name.strip(), category.strip(), cost, sell, qty, reorder),
                        )
                        st.success(f"Added {name}")
                    except sqlite3.IntegrityError:
                        st.error("A product with this SKU already exists.")

    with tab_edit:
        products = get_products()
        if products.empty:
            st.info("No products to edit.")
            return
        labels = {f"{r['sku']} - {r['name']}": r["id"] for _, r in products.iterrows()}
        choice = st.selectbox("Select product", list(labels))
        p = products[products["id"] == labels[choice]].iloc[0]

        with st.form("edit_product"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Name", p["name"])
            category = c2.text_input("Category", p["category"] or "")
            cost = c1.number_input("Cost price", min_value=0.0, value=float(p["cost_price"]), step=0.5)
            sell = c2.number_input("Selling price", min_value=0.0, value=float(p["sell_price"]), step=0.5)
            qty = c1.number_input("Quantity (manual correction)", min_value=0, value=int(p["quantity"]), step=1)
            reorder = c2.number_input("Reorder level", min_value=0, value=int(p["reorder_level"]), step=1)
            if st.form_submit_button("Save changes"):
                execute(
                    """UPDATE products SET name=?, category=?, cost_price=?, sell_price=?,
                       quantity=?, reorder_level=? WHERE id=?""",
                    (name, category, cost, sell, qty, reorder, int(p["id"])),
                )
                st.success("Saved")
                st.rerun()

        st.divider()
        if st.button("🗑️ Delete this product", type="secondary"):
            used = query("SELECT COUNT(*) AS n FROM transactions WHERE product_id=?",
                         (int(p["id"]),))["n"][0]
            if used:
                st.error("Cannot delete: this product has transactions.")
            else:
                execute("DELETE FROM products WHERE id=?", (int(p["id"]),))
                st.success("Deleted")
                st.rerun()


def page_new_transaction():
    st.title("💰 New transaction")
    products = get_products()
    if products.empty:
        st.info("Add a product first.")
        return

    labels = {f"{r['sku']} - {r['name']} (stock: {r['quantity']})": r for _, r in products.iterrows()}
    tx_type = st.radio("Type", ["Sale", "Purchase"], horizontal=True)
    choice = st.selectbox("Product", list(labels))
    p = labels[choice]
    default_price = float(p["sell_price"] if tx_type == "Sale" else p["cost_price"])

    with st.form("new_tx", clear_on_submit=True):
        c1, c2 = st.columns(2)
        tx_date = c1.date_input("Date", date.today())
        qty = c2.number_input("Quantity", min_value=1, step=1)
        price = c1.number_input("Unit price", min_value=0.0, value=default_price, step=0.5)
        party = c2.text_input("Customer" if tx_type == "Sale" else "Supplier")
        note = st.text_input("Note")
        st.caption(f"Total: {qty * price:,.2f}")
        if st.form_submit_button("Save transaction"):
            if tx_type == "Sale" and qty > int(p["quantity"]):
                st.error(f"Not enough stock. Available: {int(p['quantity'])}")
            else:
                record_transaction(tx_date, int(p["id"]), tx_type, int(qty), price, party, note)
                st.success(f"{tx_type} recorded.")


def page_history():
    st.title("🧾 Transaction history")
    df = query(
        """SELECT t.id, t.tx_date AS date, p.sku, p.name AS product, t.tx_type AS type,
                  t.quantity, t.unit_price, t.quantity * t.unit_price AS total,
                  t.party, t.note
           FROM transactions t JOIN products p ON p.id = t.product_id
           ORDER BY t.tx_date DESC, t.id DESC"""
    )
    if df.empty:
        st.info("No transactions yet.")
        return

    c1, c2 = st.columns(2)
    types = c1.multiselect("Type", ["Sale", "Purchase"], default=["Sale", "Purchase"])
    search = c2.text_input("Search product / party")
    df = df[df["type"].isin(types)]
    if search:
        s = search.lower()
        df = df[df.apply(lambda r: s in f"{r['product']} {r['party']} {r['sku']}".lower(), axis=1)]

    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button("Download CSV", df.to_csv(index=False), "transactions.csv", "text/csv")

    with st.expander("Delete a transaction (stock will be reversed)"):
        tx_id = st.number_input("Transaction ID", min_value=1, step=1)
        if st.button("Delete transaction"):
            delete_transaction(int(tx_id))
            st.success("Deleted and stock adjusted.")
            st.rerun()


# ---------------------------------------------------------------- main
def main():
    init_db()
    if not check_password():
        return
    page = st.sidebar.radio(
        "Menu", ["Dashboard", "Products", "New transaction", "History"]
    )
    {
        "Dashboard": page_dashboard,
        "Products": page_products,
        "New transaction": page_new_transaction,
        "History": page_history,
    }[page]()


main()
