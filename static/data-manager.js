"use strict";

// =====================================================================
// DigiBuzz AI Calling Agent — Product/Data Manager
// =====================================================================

const productState = {
  allProducts: [],
  editingId: null,
};

// =====================================================================
// Load & Render
// =====================================================================
async function loadProducts() {
  const tbody = document.getElementById("productTableBody");
  tbody.innerHTML = '<tr><td colspan="7" class="loading-row">Loading products…</td></tr>';

  try {
    const res = await fetch("/api/products");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    productState.allProducts = await res.json();
    populateCategoryFilter();
    renderProductTable(productState.allProducts);
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" class="loading-row" style="color:var(--red)">Failed to load products: ${err.message}</td></tr>`;
  }
}

function renderProductTable(products) {
  const tbody = document.getElementById("productTableBody");
  const countEl = document.getElementById("productCount");

  countEl.textContent = `${products.length} record${products.length !== 1 ? "s" : ""}`;

  if (products.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="loading-row">No products found.</td></tr>';
    return;
  }

  tbody.innerHTML = products
    .map(
      (p) => `
      <tr data-id="${p.id}">
        <td><strong>#${p.id}</strong></td>
        <td class="td-truncate" title="${escProd(p.name)}">${escProd(p.name)}</td>
        <td>${escProd(p.category)}</td>
        <td>${escProd(p.brand)}</td>
        <td>PKR ${Number(p.price).toLocaleString()}</td>
        <td>${stockBadge(p.stock_status)}</td>
        <td>
          <div class="action-cell">
            <button class="btn btn-sm btn-edit-sm" onclick="openEditProductModal(${p.id})">✏️ Edit</button>
            <button class="btn btn-sm btn-danger-sm" onclick="deleteProduct(${p.id})">🗑️ Delete</button>
          </div>
        </td>
      </tr>`
    )
    .join("");
}

function stockBadge(status) {
  const map = {
    in_stock: '<span class="badge in-stock">In Stock</span>',
    limited: '<span class="badge limited">Limited</span>',
    out_of_stock: '<span class="badge out-of-stock">Out of Stock</span>',
  };
  return map[status] || `<span class="badge">${escProd(status)}</span>`;
}

// =====================================================================
// Category Filter
// =====================================================================
function populateCategoryFilter() {
  const sel = document.getElementById("productCategoryFilter");
  const current = sel.value;
  const categories = [...new Set(productState.allProducts.map((p) => p.category))].sort();

  sel.innerHTML = '<option value="">All Categories</option>';
  categories.forEach((cat) => {
    const opt = document.createElement("option");
    opt.value = cat;
    opt.textContent = cat;
    if (cat === current) opt.selected = true;
    sel.appendChild(opt);
  });
}

function initProductFilters() {
  const searchInput = document.getElementById("productSearch");
  const catFilter = document.getElementById("productCategoryFilter");

  function applyFilters() {
    const q = searchInput.value.toLowerCase().trim();
    const cat = catFilter.value;

    const filtered = productState.allProducts.filter((p) => {
      const matchesCat = !cat || p.category === cat;
      const matchesQ =
        !q ||
        p.name.toLowerCase().includes(q) ||
        p.brand.toLowerCase().includes(q) ||
        p.description.toLowerCase().includes(q);
      return matchesCat && matchesQ;
    });

    renderProductTable(filtered);
  }

  if (searchInput) searchInput.addEventListener("input", applyFilters);
  if (catFilter) catFilter.addEventListener("change", applyFilters);
}

// =====================================================================
// Modal — Open / Close
// =====================================================================
function openAddProductModal() {
  productState.editingId = null;
  document.getElementById("productModalTitle").textContent = "Add Product";
  document.getElementById("productName").value = "";
  document.getElementById("productBrand").value = "";
  document.getElementById("productCategory").value = "";
  document.getElementById("productPrice").value = "";
  document.getElementById("productStock").value = "in_stock";
  document.getElementById("productDescription").value = "";
  hideProductError();
  document.getElementById("productModal").classList.remove("hidden");
  document.getElementById("productName").focus();
}

function openEditProductModal(id) {
  const product = productState.allProducts.find((p) => p.id === id);
  if (!product) return;
  productState.editingId = id;
  document.getElementById("productModalTitle").textContent = "Edit Product";
  document.getElementById("productName").value = product.name;
  document.getElementById("productBrand").value = product.brand;
  document.getElementById("productCategory").value = product.category;
  document.getElementById("productPrice").value = product.price;
  document.getElementById("productStock").value = product.stock_status;
  document.getElementById("productDescription").value = product.description;
  hideProductError();
  document.getElementById("productModal").classList.remove("hidden");
  document.getElementById("productName").focus();
}

function closeProductModal() {
  document.getElementById("productModal").classList.add("hidden");
  productState.editingId = null;
}

function showProductError(msg) {
  const el = document.getElementById("productFormError");
  el.textContent = msg;
  el.classList.remove("hidden");
}

function hideProductError() {
  document.getElementById("productFormError").classList.add("hidden");
}

// =====================================================================
// Save Product (POST or PUT)
// =====================================================================
async function saveProduct() {
  const name = document.getElementById("productName").value.trim();
  const brand = document.getElementById("productBrand").value.trim();
  const category = document.getElementById("productCategory").value.trim();
  const priceRaw = document.getElementById("productPrice").value.trim();
  const stock_status = document.getElementById("productStock").value;
  const description = document.getElementById("productDescription").value.trim();

  if (!name) { showProductError("Product name is required."); return; }
  if (!brand) { showProductError("Brand is required."); return; }
  if (!category) { showProductError("Category is required."); return; }
  if (!priceRaw || isNaN(Number(priceRaw)) || Number(priceRaw) < 0) {
    showProductError("A valid price (PKR) is required."); return;
  }

  hideProductError();

  const price = parseInt(priceRaw, 10);
  const payload = { name, brand, category, price, stock_status, description };

  const saveBtn = document.getElementById("productModalSave");
  saveBtn.disabled = true;
  saveBtn.textContent = "Saving…";

  try {
    const isEdit = productState.editingId !== null;
    const url = isEdit ? `/api/products/${productState.editingId}` : "/api/products";
    const method = isEdit ? "PUT" : "POST";

    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    closeProductModal();
    await loadProducts();
  } catch (err) {
    showProductError("Save failed: " + err.message);
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = "Save Product";
  }
}

// =====================================================================
// Delete Product
// =====================================================================
async function deleteProduct(id) {
  const product = productState.allProducts.find((p) => p.id === id);
  const label = product ? `"${product.name}"` : `#${id}`;
  if (!confirm(`Delete product ${label}?\n\nThis cannot be undone.`)) return;

  try {
    const res = await fetch(`/api/products/${id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 204) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    await loadProducts();
  } catch (err) {
    alert("Delete failed: " + err.message);
  }
}

// =====================================================================
// Escape helper (local scope)
// =====================================================================
function escProd(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// =====================================================================
// Initialization
// =====================================================================
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("btnAddProduct").addEventListener("click", openAddProductModal);
  document.getElementById("productModalClose").addEventListener("click", closeProductModal);
  document.getElementById("productModalCancel").addEventListener("click", closeProductModal);
  document.getElementById("productModalSave").addEventListener("click", saveProduct);

  // Close modal on overlay click
  document.getElementById("productModal").addEventListener("click", (e) => {
    if (e.target === document.getElementById("productModal")) closeProductModal();
  });

  initProductFilters();

  // Load products if starting on products page
  if (document.getElementById("page-products").classList.contains("active")) {
    loadProducts();
  }
});
