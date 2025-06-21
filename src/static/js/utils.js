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

    // Check if the container has a <pre> tag (new structure)
    const preElement = container.querySelector('pre');
    let textToCopy = "";

    if (preElement) {
        textToCopy = preElement.textContent || "";
    } else {
        // Fallback to old method if <pre> tag is not found (for robustness or during transition)
        const lines = container.querySelectorAll('div.tree-line');
        lines.forEach((line, index) => {
            const prefixSpan = line.querySelector('span.prefix');
            const nameSpan = line.querySelector('span.name-text');
            const nameContent = line.querySelector('a')?.textContent || (nameSpan ? nameSpan.textContent : '');
            const prefixHtml = prefixSpan ? prefixSpan.innerHTML : '';
            const prefixText = prefixHtml.replace(/ /g, ' ');
            if (index > 0) { textToCopy += '\n'; }
            textToCopy += prefixText + nameContent.trim();
        });
    }

    const button = document.querySelector('[onclick="copyDirectoryStructureText()"]');
    if (!button) return;
    const originalContent = button.innerHTML;
    navigator.clipboard.writeText(textToCopy.trim())
        .then(() => { button.innerHTML = 'Copied!'; setTimeout(() => { button.innerHTML = originalContent; }, 1000); })
        .catch(err => { button.innerHTML = 'Failed'; setTimeout(() => { button.innerHTML = originalContent; }, 1000); });
}

// Copy full digest function
function copyFullDigest() {
    const treeContainer = document.getElementById('directory-structure-container');
    let treeText = "";

    if (treeContainer) {
        const preElement = treeContainer.querySelector('pre');
        if (preElement) {
            treeText = preElement.textContent || "";
        } else {
            // Fallback to old method
            const lines = treeContainer.querySelectorAll('div.tree-line');
            lines.forEach((line, index) => {
                const prefixSpan = line.querySelector('span.prefix');
                const nameSpan = line.querySelector('span.name-text');
                const nameContent = line.querySelector('a')?.textContent || (nameSpan ? nameSpan.textContent : '');
                const prefixHtml = prefixSpan ? prefixSpan.innerHTML : '';
                const prefixText = prefixHtml.replace(/ /g, ' ');
                if (index > 0) { treeText += '\n'; }
                treeText += prefixText + nameContent.trim();
            });
        }
    }
    const filesContentElement = document.getElementById('result-text'); // Use ID selector
    const filesContent = filesContentElement ? filesContentElement.value : '';

    const fullDigest = `${treeText.trim()}\n\n${filesContent}`;
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
    const slider = document.getElementById('file_size');
    const sizeValueDisplay = document.getElementById('size_value');

    if (!slider || !sizeValueDisplay) {
        return;
    }

    function updateSliderAppearance() {
        const value = parseInt(slider.value, 10);
        const max = parseInt(slider.max, 10);

        const sizeInKB = logSliderToSize(value);
        sizeValueDisplay.textContent = formatSize(sizeInKB);

        const percentage = (value / max) * 100;
        slider.style.backgroundSize = percentage + '% 100%';
    }

    // Initial update
    updateSliderAppearance();

    // Add event listener for changes
    slider.addEventListener('input', updateSliderAppearance);
}

// New function to be added
function copyText(elementId) {
    const element = document.getElementById(elementId);
    const button = document.querySelector(`[onclick*="copyText('${elementId}')"]`); // Find the button that called this
    let originalButtonContent = null;
    if (button) {
        originalButtonContent = button.innerHTML;
    }

    if (element && typeof element.value !== 'undefined') { // Check if it's an input/textarea
        const textToCopy = element.value;
        navigator.clipboard.writeText(textToCopy)
            .then(() => {
                if (button && originalButtonContent !== null) {
                    button.innerHTML = 'Copied!';
                    setTimeout(() => {
                        button.innerHTML = originalButtonContent;
                    }, 1500);
                } else {
                    // Fallback or indicate success differently if button context is lost
                    console.log('Text copied to clipboard (button not found or original content issue).');
                }
            })
            .catch(err => {
                console.error('Failed to copy text: ', err);
                if (button && originalButtonContent !== null) {
                    button.innerHTML = 'Failed!';
                    setTimeout(() => {
                        button.innerHTML = originalButtonContent;
                    }, 1500);
                }
            });
    } else if (element) { // Fallback for non-input elements like <pre> or <div>
        const textToCopy = element.innerText || element.textContent;
         navigator.clipboard.writeText(textToCopy)
            .then(() => {
                if (button && originalButtonContent !== null) {
                    button.innerHTML = 'Copied!';
                    setTimeout(() => {
                        button.innerHTML = originalButtonContent;
                    }, 1500);
                }
            })
            .catch(err => {
                console.error('Failed to copy text from non-input: ', err);
                 if (button && originalButtonContent !== null) {
                    button.innerHTML = 'Failed!';
                    setTimeout(() => {
                        button.innerHTML = originalButtonContent;
                    }, 1500);
                }
            });
    } else {
        console.error('Element not found:', elementId);
        if (button && originalButtonContent !== null) {
            button.innerHTML = 'Error!';
            setTimeout(() => {
                button.innerHTML = originalButtonContent;
            }, 1500);
        }
    }
}


function setupGlobalEnterHandler() { /* ... */ }

document.addEventListener('DOMContentLoaded', () => {
    initializeSlider();
    setupGlobalEnterHandler();
});

// Make functions globally available if needed by inline handlers
window.copyText = copyText; // Ensure this is now assigned
window.copyDirectoryStructureText = copyDirectoryStructureText;
window.copyFullDigest = copyFullDigest;
window.toggleFile = toggleFile;
// window.handleSubmit = handleSubmit;
// window.submitExample = submitExample;
