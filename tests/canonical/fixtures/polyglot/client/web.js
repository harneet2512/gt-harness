// Browser-side consumer of the Python/Go route: produces an API_CALL
// edge from this file onto the /api/items handler file.
async function loadItems() {
  const r = await fetch("/api/items");
  return r.json();
}

module.exports = { loadItems };
