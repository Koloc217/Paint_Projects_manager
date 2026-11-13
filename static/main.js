/* Starts the page: runs after the other app scripts (see templates/index.html). */

fillTimeSelects();
renderFilterBar();        // filters of the project list (static/filters.js)

// Card tab: /?card=<id> shows only the project card
const standaloneCardId = new URLSearchParams(location.search).get('card');
if (standaloneCardId) initStandaloneCard(standaloneCardId);
else loadData();

// Several tabs: refresh the data when the window gets focus again
let lastFocusRefresh = 0;
window.addEventListener('focus', () => {
    const now = Date.now();
    if (now - lastFocusRefresh > 3000) { lastFocusRefresh = now; loadData(); }
});
