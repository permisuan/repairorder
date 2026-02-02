document.addEventListener("DOMContentLoaded", function() {

    // 1. Color Coding Logic
    const statusColors = {
        'open': '#e5e7eb',          // Light Gray
        'parts_ordered': '#fef08a', // Yellow
        'wip': '#bbf7d0',           // Green (Active)
        'complete': '#bfdbfe'       // Blue
    };

    function updateRowColor(row, status) {
        // Default to white if status not found
        const color = statusColors[status] || '#ffffff';
        row.style.backgroundColor = color;
        
        // Visual highlight for Active Job (WIP)
        if (status === 'wip') {
            row.style.border = "3px solid #22c55e"; // Green border
        } else {
            row.style.border = "1px solid #ddd"; // Standard border
        }
    }

    // 2. Sorting Logic (Move WIP to top, others below)
    function sortJobs() {
        const container = document.getElementById('job-container');
        if (!container) return; // Safety check

        const rows = Array.from(container.getElementsByClassName('job-row'));

        rows.sort((a, b) => {
            const statusA = a.querySelector('.status-select').value;
            const statusB = b.querySelector('.status-select').value;
            // Sort logic: WIP comes first (return -1), everything else follows
            return (statusB === 'wip') - (statusA === 'wip');
        });

        // Re-append rows in the new sorted order
        rows.forEach(row => container.appendChild(row));
    }

    // 3. Backend Communication (Fetch)
    async function sendUpdate(jobId, status, note = null) {
        try {
            // NOTE: Ensure your main.py has a route for '/update_job'
            const response = await fetch('/update_job', { 
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ job_id: jobId, status: status, note: note })
            });

            if (!response.ok) throw new Error('Server returned error');
            console.log(`Success: Job ${jobId} updated to ${status}`);

        } catch (error) {
            console.error('Save failed:', error);
            alert("⚠️ Connection Error: Change might not be saved!");
        }
    }

    // 4. Event Listeners for all Job Rows
    document.querySelectorAll('.job-row').forEach(row => {
        const statusSelect = row.querySelector('.status-select');
        const clockBtn = row.querySelector('.clock-btn');
        const jobId = row.dataset.jobId; // Requires data-job-id attribute in HTML

        // Initial Color Setup
        updateRowColor(row, statusSelect.value);

        // -- Listener: Status Dropdown Change --
        statusSelect.addEventListener('change', function() {
            const newStatus = this.value;
            
            updateRowColor(row, newStatus);
            if (newStatus === 'wip') sortJobs();
            
            // Send to backend
            sendUpdate(jobId, newStatus);
        });

        // -- Listener: Clock Button Click --
        clockBtn.addEventListener('click', function() {
            const currentStatus = statusSelect.value;

            if (currentStatus !== 'wip') {
                // === ACTION: CLOCK ON ===
                statusSelect.value = 'wip';
                updateRowColor(row, 'wip');
                
                // Update Button Visuals
                this.innerText = "CLOCK OFF";
                this.classList.remove('btn-green'); 
                this.classList.add('btn-red'); // Make button red/alert
                
                sortJobs(); // Move to top
                sendUpdate(jobId, 'wip'); // Save to backend

            } else {
                // === ACTION: CLOCK OFF (Trigger Validation) ===
                validateAndClockOff(row, statusSelect, this, jobId);
            }
        });
    });

    // 5. Validation Logic (The 3 C's)
    function validateAndClockOff(row, select, btn, jobId) {
        let note = "";
        let valid = false;

        while (!valid) {
            note = prompt(
                "STOP! Enter Tech Story.\nFormat required:\n- Cause\n- Complaint\n- Correction", 
                note // Pre-fill previous attempt if loop repeats
            );

            if (note === null) return; // User pressed Cancel, do nothing

            // Check for keywords (Case insensitive)
            const hasCause = /cause/i.test(note);
            const hasComplaint = /complaint/i.test(note);
            const hasCorrection = /correction/i.test(note);

            if (hasCause && hasComplaint && hasCorrection) {
                valid = true;
            } else {
                alert("Missing format! You must include: 'Cause', 'Complaint', and 'Correction'.");
            }
        }

        // If valid, proceed to complete
        select.value = 'complete';
        updateRowColor(row, 'complete');
        
        // Reset Button Visuals
        btn.innerText = "CLOCK ON";
        btn.classList.remove('btn-red');
        btn.classList.add('btn-green');
        
        sortJobs(); // Re-sort (completed job drops down)
        
        // Save to backend with the Note
        sendUpdate(jobId, 'complete', note);
    }
    
    // Final Initial Sort
    sortJobs();
});