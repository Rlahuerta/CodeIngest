// src/static/js/utils.js

// Function to extract the display name (without prefixes/suffixes) from a tree item
function getBaseNameFromTreeItem(element) {
    let nameElement = element.querySelector('span.name-text');
    let text = nameElement ? nameElement.textContent : '';
    return text.trim();
}

function toggleFile(element) {
    const patternInput = document.getElementById("pattern");
    if (!patternInput) return;
    const patternFiles = patternInput.value ? patternInput.value.split(",").map(item => item.trim()).filter(Boolean) : [];
    const lineElement = element.closest('div.tree-line');
    if (!lineElement) return;
    lineElement.classList.toggle('line-through');
    lineElement.classList.toggle('text-gray-500');
    const relativePath = lineElement.dataset.relativePath;
    if (!relativePath || relativePath === '.' || lineElement.dataset.depth === '0') return;
    const patternToToggle = relativePath;
    const fileIndex = patternFiles.indexOf(patternToToggle);
    if (fileIndex !== -1) { patternFiles.splice(fileIndex, 1); }
    else { patternFiles.push(patternToToggle); }
    patternInput.value = patternFiles.filter(Boolean).join(", ");
}

 // Function to copy the directory structure text (reconstructed)
function copyDirectoryStructureText() {
    const container = document.getElementById('directory-structure-container');
    if (!container) return;
    let textToCopy = "";
    const lines = container.querySelectorAll('div.tree-line');
    lines.forEach((line, index) => {
        const prefixSpan = line.querySelector('span.prefix');
        const nameSpan = line.querySelector('span.name-text');
        const nameContent = line.querySelector('a')?.textContent || (nameSpan ? nameSpan.textContent : '');
        // Get innerHTML to capture  , then replace it and the visual chars
        const prefixHtml = prefixSpan ? prefixSpan.innerHTML : '';
        // Replace   with space, keep visual chars for copy
        const prefixText = prefixHtml.replace(/ /g, ' ');
        // Add newline before if not the first line
        if (index > 0) { textToCopy += '\n'; }
        textToCopy += prefixText + nameContent.trim();
    });
    const button = document.querySelector('[onclick="copyDirectoryStructureText()"]');
    if (!button) return;
    const originalContent = button.innerHTML;
    navigator.clipboard.writeText(textToCopy.trim()) // Trim only at the end
        .then(() => { button.innerHTML = 'Copied!'; setTimeout(() => { button.innerHTML = originalContent; }, 1000); })
        .catch(err => { button.innerHTML = 'Failed'; setTimeout(() => { button.innerHTML = originalContent; }, 1000); });
}

// Copy full digest function
function copyFullDigest() {
    const treeContainer = document.getElementById('directory-structure-container');
    let treeText = "";
     if (treeContainer) {
         const lines = treeContainer.querySelectorAll('div.tree-line');
         lines.forEach((line, index) => {
             const prefixSpan = line.querySelector('span.prefix');
             const nameSpan = line.querySelector('span.name-text');
             const nameContent = line.querySelector('a')?.textContent || (nameSpan ? nameSpan.textContent : '');
             // Get innerHTML to capture  , then replace it
             const prefixHtml = prefixSpan ? prefixSpan.innerHTML : '';
             const prefixText = prefixHtml.replace(/ /g, ' ');
              if (index > 0) { treeText += '\n'; }
             treeText += prefixText + nameContent.trim();
         });
    }
    const filesContent = document.querySelector('.result-text').value;
    const fullDigest = `${treeText.trim()}\n\n${filesContent}`; // Trim tree text at the end
    const button = document.querySelector('[onclick="copyFullDigest()"]');
    const originalText = button.innerHTML;
    navigator.clipboard.writeText(fullDigest).then(() => {
        button.innerHTML = `<svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg> Copied!`;
        setTimeout(() => { button.innerHTML = originalText; }, 2000);
    }).catch(err => { console.error('Failed to copy text: ', err); });
}

// Slider and other functions remain the same
function logSliderToSize(position) { /* ... */ }
function initializeSlider() { /* ... */ }
function formatSize(sizeInKB) { /* ... */ }
function setupGlobalEnterHandler() { /* ... */ }

document.addEventListener('DOMContentLoaded', () => {
    initializeSlider();
    setupGlobalEnterHandler();
});

// Make functions globally available if needed by inline handlers
window.copyText = copyText;
window.copyDirectoryStructureText = copyDirectoryStructureText; // Ensure this is global
window.copyFullDigest = copyFullDigest;       // Ensure this is global
window.toggleFile = toggleFile;               // Ensure this is global
window.handleSubmit = handleSubmit;           // Assuming handleSubmit exists elsewhere or is defined above
// window.submitExample = submitExample; // If submitExample is needed globally