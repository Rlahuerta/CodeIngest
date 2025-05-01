// --- Add this function to toggle input visibility ---
function toggleSourceInput(type) {
    const urlPathInputDiv = document.getElementById('urlPathInputDiv');
    const zipFileInputDiv = document.getElementById('zipFileInputDiv');
    const urlPathInput = document.getElementById('input_text');
    const zipFileInput = document.getElementById('zip_file_input');

    // Ensure elements exist before manipulating them
    if (!urlPathInputDiv || !zipFileInputDiv || !urlPathInput || !zipFileInput) {
        console.error("One or more input elements not found for toggling.");
        return;
    }

    if (type === 'url_path') {
        urlPathInputDiv.style.display = 'block';
        zipFileInputDiv.style.display = 'none';
        urlPathInput.required = true; // Make URL/Path required
        zipFileInput.required = false; // Make ZIP not required
        zipFileInput.value = ''; // Clear file input if switching away
    } else if (type === 'zip_file') {
        urlPathInputDiv.style.display = 'none';
        zipFileInputDiv.style.display = 'block';
        urlPathInput.required = false; // Make URL/Path not required
        zipFileInput.required = true; // Make ZIP required
        urlPathInput.value = ''; // Clear text input if switching away
    }
}
// --- End of added function ---


// Copy functionality (remains the same)
function copyText(className) {
    let textToCopy;

    if (className === 'directory-structure') {
        // For directory structure, get the hidden input value
        const hiddenInput = document.getElementById('directory-structure-content');
        if (!hiddenInput) return;
        textToCopy = hiddenInput.value;
    } else {
        // For other elements, get the textarea value
        const textarea = document.querySelector('.' + className);
        if (!textarea) return;
        textToCopy = textarea.value;
    }

    const button = document.querySelector(`button[onclick="copyText('${className}')"]`);
    if (!button) return;

    // Copy text
    navigator.clipboard.writeText(textToCopy)
        .then(() => {
            // Store original content
            const originalContent = button.innerHTML;

            // Change button content
            button.innerHTML = 'Copied!';

            // Reset after 1 second
            setTimeout(() => {
                button.innerHTML = originalContent;
            }, 1000);
        })
        .catch(err => {
            // Show error in button
            const originalContent = button.innerHTML;
            button.innerHTML = 'Failed to copy';
            setTimeout(() => {
                button.innerHTML = originalContent;
            }, 1000);
        });
}


function handleSubmit(event, showLoading = false) {
    event.preventDefault();
    const form = event.target || document.getElementById('ingestForm');
    if (!form) return;

    const submitButton = form.querySelector('button[type="submit"]');
    if (!submitButton) return;

    // --- Use FormData to handle file uploads correctly ---
    const formData = new FormData(form);

    // --- No need to manually update form data for standard inputs like slider, pattern ---
    // --- FormData captures them automatically ---
    // --- Remove manual updates for max_file_size, pattern_type, pattern ---

    const originalContent = submitButton.innerHTML;
    const currentStars = document.getElementById('github-stars')?.textContent;

    if (showLoading) {
        submitButton.disabled = true;
        submitButton.innerHTML = `
            <div class="flex items-center justify-center">
                <svg class="animate-spin h-5 w-5 text-gray-900" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span class="ml-2">Processing...</span>
            </div>
        `;
        submitButton.classList.add('bg-[#ffb14d]'); // Indicate loading state visually
    }

    // Submit the form using FormData
    fetch(form.action, {
        method: 'POST',
        body: formData // Send FormData object directly
    })
        .then(response => response.text())
        .then(html => {
            // Store the star count before updating the DOM
            const starCount = currentStars;

            // Replace the entire body content with the new HTML
            document.body.innerHTML = html;

            // Wait for next tick to ensure DOM is updated
            setTimeout(() => {
                // Reinitialize slider functionality
                initializeSlider();
                // Re-attach event listeners or re-run setup if needed for dynamic content
                setupGlobalEnterHandler(); // Re-attach enter handler
                // Restore radio button state if possible (might need more complex state management)
                const formAfterLoad = document.getElementById('ingestForm');
                 if (formAfterLoad) {
                     const sourceType = formData.get('source_type'); // Get original source type
                     const radio = formAfterLoad.querySelector(`input[name="source_type"][value="${sourceType}"]`);
                     if (radio) {
                         radio.checked = true;
                         toggleSourceInput(sourceType); // Ensure correct input is visible
                     }
                 }


                const starsElement = document.getElementById('github-stars');
                if (starsElement && starCount) {
                    starsElement.textContent = starCount;
                }

                // Scroll to results if they exist
                const resultsSection = document.querySelector('[data-results]');
                if (resultsSection) {
                    resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            }, 0);
        })
        .catch(error => {
            console.error("Form submission error:", error); // Log the error
            // Restore button state on error
            if (submitButton) {
                submitButton.disabled = false;
                submitButton.innerHTML = originalContent;
                submitButton.classList.remove('bg-[#ffb14d]');
            }
            // Optionally display an error message to the user
            const errorDiv = document.getElementById('error-message'); // Assuming you have an error div
             if (errorDiv) {
                 errorDiv.textContent = 'An error occurred during submission. Please try again.';
                 errorDiv.style.display = 'block';
             }
        });
}

function copyFullDigest() {
    const directoryStructureElement = document.getElementById('directory-structure-content');
    const filesContentElement = document.querySelector('.result-text');

    // Check if elements exist
    if (!directoryStructureElement || !filesContentElement) {
        console.error("Could not find elements for copying full digest.");
        return;
    }

    const directoryStructure = directoryStructureElement.value;
    const filesContent = filesContentElement.value;

    const fullDigest = `${directoryStructure}\n\nFiles Content:\n\n${filesContent}`;
    const button = document.querySelector('[onclick="copyFullDigest()"]');
     if (!button) {
         console.error("Copy button not found.");
         return;
     }
    const originalText = button.innerHTML;

    navigator.clipboard.writeText(fullDigest).then(() => {
        button.innerHTML = `
            <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
            </svg>
            Copied!
        `;

        setTimeout(() => {
            button.innerHTML = originalText;
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy text: ', err);
    });
}

// Add the logSliderToSize helper function
function logSliderToSize(position) {
    const minp = 0;
    const maxp = 500;
    const minv = Math.log(1);
    const maxv = Math.log(102400);

    // Ensure position is within bounds
    position = Math.max(minp, Math.min(position, maxp));

    const value = Math.exp(minv + (maxv - minv) * Math.pow(position / maxp, 1.5));
    return Math.round(value);
}

// Move slider initialization to a separate function
function initializeSlider() {
    const slider = document.getElementById('file_size');
    const sizeValue = document.getElementById('size_value');

    if (!slider || !sizeValue) return;

    function updateSlider() {
        const value = logSliderToSize(slider.value);
        sizeValue.textContent = formatSize(value);
        // Calculate percentage for background fill
        const percentage = ((slider.value - slider.min) / (slider.max - slider.min)) * 100;
        slider.style.backgroundSize = `${percentage}% 100%`;
    }

    // Update on slider change
    slider.addEventListener('input', updateSlider);

    // Initialize slider position and background on load
    updateSlider();
}

// Add helper function for formatting size
function formatSize(sizeInKB) {
    if (sizeInKB >= 1024) {
        return Math.round(sizeInKB / 1024) + 'mb';
    }
    return Math.round(sizeInKB) + 'kb';
}

// Add this new function
function setupGlobalEnterHandler() {
    // Remove existing listener if it exists to prevent duplicates
    document.removeEventListener('keydown', handleGlobalEnter);
    // Add the listener
    document.addEventListener('keydown', handleGlobalEnter);
}

// Define the handler function separately
function handleGlobalEnter(event) {
    // Check if Enter key was pressed, not inside a textarea, and the form exists
    if (event.key === 'Enter' && !event.target.matches('textarea')) {
        const form = document.getElementById('ingestForm');
        if (form) {
            // Prevent default Enter key behavior (like submitting the form traditionally)
            event.preventDefault();
            // Trigger our custom submit handler
            handleSubmit(new SubmitEvent('submit', { target: form, bubbles: true, cancelable: true }), true);
        }
    }
}


// Initialize slider and other setup on page load
document.addEventListener('DOMContentLoaded', () => {
    initializeSlider();
    setupGlobalEnterHandler();
    // Ensure the correct input is visible on initial load based on checked radio
    const checkedRadio = document.querySelector('input[name="source_type"]:checked');
    if (checkedRadio) {
        toggleSourceInput(checkedRadio.value);
    } else {
        // Default to URL/Path if nothing is checked (shouldn't happen with 'checked' in HTML)
        toggleSourceInput('url_path');
    }
});

// Make sure these are available globally if needed by inline HTML event handlers
window.copyText = copyText;
window.handleSubmit = handleSubmit;
window.initializeSlider = initializeSlider;
window.formatSize = formatSize;
window.toggleSourceInput = toggleSourceInput; // Make toggle function global
window.copyFullDigest = copyFullDigest; // Ensure this is global too

// --- Add submitExample function if used in index.jinja ---
function submitExample(repoUrl) {
    // Ensure URL radio is checked when an example is clicked
    const urlRadio = document.querySelector('input[name="source_type"][value="url_path"]');
    if (urlRadio) {
        urlRadio.checked = true;
        toggleSourceInput('url_path'); // Make sure the URL input is visible
    }
    const input = document.getElementById('input_text'); // Target the URL input
    if (input) {
        input.value = repoUrl;
        input.focus();
        // Optional: Automatically submit the form after setting the example
        // const form = document.getElementById('ingestForm');
        // if (form) {
        //     handleSubmit(new Event('submit', { target: form, bubbles: true, cancelable: true }), true);
        // }
    }
}
window.submitExample = submitExample; // Make it global
// --- End submitExample ---
