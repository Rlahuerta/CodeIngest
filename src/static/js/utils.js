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

function logSliderToSize(position) {
    const maxSliderPos = 500; // Max position of the slider
    const minKB = 1;          // Min file size in KB
    const maxKB = 102400;     // Max file size in KB (100MB)

    // Logarithmic scale parameters
    const minv = Math.log(minKB);
    const maxv = Math.log(maxKB);

    // Calculate the logarithmic value: position^1.5 normalized to range, then scaled
    const scale = (maxv - minv) / Math.pow(maxSliderPos, 1.5);
    let sizeInKB = Math.exp(minv + scale * Math.pow(position, 1.5));

    return Math.round(sizeInKB);
}

function formatSize(sizeInKB) {
    if (sizeInKB < 1024) {
        return sizeInKB + ' KB';
    } else {
        const sizeInMB = sizeInKB / 1024;
        return sizeInMB.toFixed(1) + ' MB';
    }
}

function initializeSlider() {
    console.log("Attempting to initialize slider..."); // New log
    const slider = document.getElementById('file_size');
    const sizeValueDisplay = document.getElementById('size_value');

    if (!slider) {
        console.error("Slider element #file_size not found."); // Changed to error
        return;
    }
    if (!sizeValueDisplay) {
        console.error("Size display element #size_value not found."); // Changed to error
        return;
    }
    console.log("Slider and display elements found:", slider, sizeValueDisplay); // New log

    function updateSliderAppearance() {
        console.log("updateSliderAppearance called."); // New log
        const value = parseInt(slider.value, 10);
        const max = parseInt(slider.max, 10);

        console.log("Slider value:", value, "Max:", max); // New log

        const sizeInKB = logSliderToSize(value);
        console.log("Calculated sizeInKB:", sizeInKB); // New log
        sizeValueDisplay.textContent = formatSize(sizeInKB);

        const percentage = (value / max) * 100;
        console.log("Calculated percentage for background:", percentage); // New log
        slider.style.backgroundSize = percentage + '% 100%';
        console.log("Updated text to:", sizeValueDisplay.textContent, "and backgroundSize to:", slider.style.backgroundSize); // New log
    }

    // Initial update
    console.log("Calling initial updateSliderAppearance..."); // New log
    updateSliderAppearance();

    // Add event listener for changes
    slider.addEventListener('input', function() { // Added function wrapper for logging
        console.log("Slider 'input' event fired."); // New log
        updateSliderAppearance();
    });
    console.log("Event listener added to slider."); // New log
}

function setupGlobalEnterHandler() { /* ... */ } // Assuming this is defined elsewhere or not critical for this task

document.addEventListener('DOMContentLoaded', () => {
    initializeSlider();
    setupGlobalEnterHandler(); // Assuming this is defined elsewhere or not critical for this task
});

// Make functions globally available if needed by inline handlers
// window.copyText = copyText; // copyText is not defined in this file
window.copyDirectoryStructureText = copyDirectoryStructureText; // Ensure this is global
window.copyFullDigest = copyFullDigest;       // Ensure this is global
window.toggleFile = toggleFile;               // Ensure this is global
// window.handleSubmit = handleSubmit;        // handleSubmit is not defined in this file
// window.submitExample = submitExample; // submitExample is not defined in this file
