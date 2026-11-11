"use strict";

// =====================================================================
// AI Calling Agent — Product Catalog Manager (Multi-Tenant)
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
  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="8" class="loading-row">Loading products…</td></tr>';
  }

  try {
    const res = await fetch("/api/products", { credentials: "same-origin" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    productState.allProducts = await res.json();
    populateCategoryFilter();
    renderProductTable(productState.allProducts);
  } catch (err) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="8" class="loading-row" style="color:var(--red)">Failed to load products: ${window.escapeHtml ? window.escapeHtml(err.message) : err.message}</td></tr>`;
    }
  }
}

function renderProductTable(products) {
  const tbody = document.getElementById("productTableBody");
  const countEl = document.getElementById("productCount");
  if (!tbody) return;

  if (countEl) countEl.textContent = `${products.length} product${products.length !== 1 ? "s" : ""}`;

  if (!products || products.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="loading-row">No products match your search criteria.</td></tr>';
    return;
  }

  tbody.innerHTML = products
    .map((p) => {
      const isOwner = authState.user && p.user_id === authState.user.id;
      const canEdit = authState.isAdmin || isOwner;

      let scopeBadge = '<span class="badge" style="background:var(--surface-card);color:var(--text-muted);font-size:.7rem;">Platform</span>';
      if (p.user_id) {
        const ownerLabel = authState.isAdmin ? (p.owner_name || "Custom") : (isOwner ? "Yours" : "Custom");
        scopeBadge = `<span class="badge" style="background:var(--brand-light);color:var(--brand);font-size:.7rem;">${escProd(ownerLabel)}</span>`;
      }

      return `
      <tr data-id="${p.id}">
        <td><strong>#${p.id}</strong></td>
        <td>
          <div style="font-weight:600;">${escProd(p.name)}</div>
          <div style="font-size:0.75rem;color:var(--text-muted);">${escProd(p.description || "")}</div>
        </td>
        <td><span class="badge" style="background:var(--surface-card);color:var(--text-muted);">${escProd(p.category)}</span></td>
        <td>${escProd(p.brand)}</td>
        <td><strong>${formatPKR(p.price)}</strong></td>
        <td>${stockBadge(p.stock_status)}</td>
        <td>${scopeBadge}</td>
        <td>
          <div class="action-cell">
            ${
              canEdit
                ? `
              <button class="btn btn-sm btn-edit-sm" onclick="openEditProductModal(${p.id})">✏️ Edit</button>
              <button class="btn btn-sm btn-danger-sm" onclick="deleteProduct(${p.id})">🗑️ Delete</button>
            `
                : '<span style="font-size:.75rem;color:var(--text-muted);">Default</span>'
            }
          </div>
        </td>
      </tr>`;
    })
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
  if (!sel) return;
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
  const categoryFilter = document.getElementById("productCategoryFilter");

  function applyProductFilter() {
    const q = (searchInput ? searchInput.value : "").toLowerCase().trim();
    const selectedCategory = categoryFilter ? categoryFilter.value : "";

    const filtered = productState.allProducts.filter((p) => {
      const matchesCategory = !selectedCategory || p.category === selectedCategory;
      const matchesQ =
        !q ||
        p.name.toLowerCase().includes(q) ||
        p.brand.toLowerCase().includes(q) ||
        p.category.toLowerCase().includes(q) ||
        (p.description && p.description.toLowerCase().includes(q));
      return matchesCategory && matchesQ;
    });

    renderProductTable(filtered);
  }

  if (searchInput) searchInput.addEventListener("input", applyProductFilter);
  if (categoryFilter) categoryFilter.addEventListener("change", applyProductFilter);
}

// =====================================================================
// Modal — Open / Close
// =====================================================================
function openAddProductModal() {
  if (!authState.isLoggedIn) {
    if (typeof openAuthModal === "function") {
      openAuthModal("login");
    } else {
      alert("Please sign in to add your custom products.");
    }
    return;
  }

  productState.editingId = null;
  const title = document.getElementById("productModalTitle");
  if (title) title.textContent = "Add Product";
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
  const p = productState.allProducts.find((item) => item.id === id);
  if (!p) return;

  const isOwner = authState.user && p.user_id === authState.user.id;
  if (!authState.isAdmin && !isOwner) {
    alert("You do not have permission to modify this product.");
    return;
  }

  productState.editingId = id;
  const title = document.getElementById("productModalTitle");
  if (title) title.textContent = "Edit Product";
  document.getElementById("productName").value = p.name;
  document.getElementById("productBrand").value = p.brand;
  document.getElementById("productCategory").value = p.category;
  document.getElementById("productPrice").value = p.price;
  document.getElementById("productStock").value = p.stock_status;
  document.getElementById("productDescription").value = p.description || "";
  hideProductError();
  document.getElementById("productModal").classList.remove("hidden");
  document.getElementById("productName").focus();
}

function closeProductModal() {
  const modal = document.getElementById("productModal");
  if (modal) modal.classList.add("hidden");
  productState.editingId = null;
}

function showProductError(msg) {
  const el = document.getElementById("productFormError");
  if (el) {
    el.textContent = msg;
    el.classList.remove("hidden");
  }
}

function hideProductError() {
  const el = document.getElementById("productFormError");
  if (el) {
    el.textContent = "";
    el.classList.add("hidden");
  }
}

// =====================================================================
// Save (Create or Update)
// =====================================================================
async function saveProduct() {
  hideProductError();
  const name = document.getElementById("productName").value.trim();
  const brand = document.getElementById("productBrand").value.trim();
  const category = document.getElementById("productCategory").value.trim();
  const priceRaw = document.getElementById("productPrice").value;
  const stock_status = document.getElementById("productStock").value;
  const description = document.getElementById("productDescription").value.trim();
  const saveBtn = document.getElementById("productModalSave");

  if (!name) {
    showProductError("Product Name is required.");
    return;
  }
  if (!brand) {
    showProductError("Brand is required.");
    return;
  }
  if (!category) {
    showProductError("Category is required.");
    return;
  }
  const price = parseInt(priceRaw, 10);
  if (isNaN(price) || price < 0) {
    showProductError("Please enter a valid price in PKR.");
    return;
  }

  saveBtn.disabled = true;
  saveBtn.textContent = "Saving…";

  const payload = { name, brand, category, price, stock_status, description };
  const isEdit = productState.editingId !== null;
  const url = isEdit ? `/api/products/${productState.editingId}` : "/api/products";
  const method = isEdit ? "PUT" : "POST";

  try {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      credentials: "same-origin",
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    closeProductModal();
    await loadProducts();
  } catch (err) {
    showProductError(err.message || "Failed to save product.");
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = "Save Product";
  }
}

// =====================================================================
// Delete
// =====================================================================
async function deleteProduct(id) {
  const p = productState.allProducts.find((item) => item.id === id);
  const namePreview = p ? `"${p.name}"` : `#${id}`;

  if (!confirm(`Are you sure you want to delete ${namePreview}?`)) return;

  try {
    const res = await fetch(`/api/products/${id}`, { method: "DELETE", credentials: "same-origin" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    await loadProducts();
  } catch (err) {
    alert(`Failed to delete product: ${err.message}`);
  }
}

// =====================================================================
// Helpers
// =====================================================================
function formatPKR(val) {
  return "PKR " + Number(val || 0).toLocaleString("en-PK");
}

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
  const btnAdd = document.getElementById("btnAddProduct");
  if (btnAdd) btnAdd.addEventListener("click", openAddProductModal);

  const btnRefresh = document.getElementById("btnRefreshProducts");
  if (btnRefresh) btnRefresh.addEventListener("click", loadProducts);

  const btnCancel = document.getElementById("productModalCancel");
  if (btnCancel) btnCancel.addEventListener("click", closeProductModal);

  const btnClose = document.getElementById("productModalClose");
  if (btnClose) btnClose.addEventListener("click", closeProductModal);

  const btnSave = document.getElementById("productModalSave");
  if (btnSave) btnSave.addEventListener("click", saveProduct);

  const modal = document.getElementById("productModal");
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeProductModal();
    });
  }

  initProductFilters();
});
