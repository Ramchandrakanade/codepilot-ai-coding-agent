const $ = id => document.getElementById(id);

let uploadedProjectId = null;


// ============================================
// Example task
// ============================================

$('example').onclick = () => {
    $('task').value =
        'Add input validation to the score field and write a test for invalid values.';
};


// ============================================
// Project source selection
// ============================================

document
    .querySelectorAll('input[name="projectSource"]')
    .forEach(option => {

        option.addEventListener('change', () => {

            const uploadMode =
                $('uploadProject').checked;

            if (uploadMode) {

                $('uploadArea')
                    .classList
                    .remove('hidden');

            } else {

                $('uploadArea')
                    .classList
                    .add('hidden');

                uploadedProjectId = null;

                $('uploadStatus').textContent =
                    'Upload a ZIP containing the project you want CodePilot to analyze.';

                $('uploadStatus')
                    .className = 'upload-status';
            }
        });
    });


// ============================================
// Project ZIP upload
// ============================================

$('projectZip').addEventListener(
    'change',
    async event => {

        const file =
            event.target.files[0];

        if (!file) {
            return;
        }

        if (!file.name.toLowerCase().endsWith('.zip')) {

            $('uploadStatus').textContent =
                'Please select a .zip project file.';

            $('uploadStatus')
                .className =
                'upload-status error';

            uploadedProjectId = null;

            return;
        }

        $('uploadStatus').textContent =
            'Uploading project...';

        $('uploadStatus')
            .className =
            'upload-status';


        const formData =
            new FormData();

        formData.append(
            'project',
            file
        );


        try {

            const response =
                await fetch(
                    '/api/project/upload',
                    {
                        method: 'POST',
                        body: formData
                    }
                );


            const data =
                await response.json();


            if (!response.ok) {

                throw new Error(
                    data.error ||
                    'Project upload failed.'
                );
            }


            uploadedProjectId =
                data.upload_id;


            $('uploadStatus').textContent =
                `✓ ${data.project_name} uploaded successfully.`;

            $('uploadStatus')
                .className =
                'upload-status success';


        } catch (error) {

            uploadedProjectId = null;

            $('uploadStatus').textContent =
                error.message;

            $('uploadStatus')
                .className =
                'upload-status error';
        }
    }
);


// ============================================
// Run CodePilot
// ============================================

$('run').onclick = async () => {

    const task =
        $('task').value.trim();


    if (!task) {

        $('error').textContent =
            'Please enter a developer task.';

        $('error')
            .classList
            .remove('hidden');

        return;
    }


    const usingUpload =
        $('uploadProject').checked;


    if (
        usingUpload &&
        !uploadedProjectId
    ) {

        $('error').textContent =
            'Please upload a project ZIP first.';

        $('error')
            .classList
            .remove('hidden');

        return;
    }


    $('error')
        .classList
        .add('hidden');


    $('run').disabled = true;

    $('run').textContent =
        'Analyzing...';


    $('workspace')
        .classList
        .remove('hidden');


    $('plan').innerHTML =
        '<div class="empty">Analyzing task and scanning codebase...</div>';


    $('files').innerHTML = '';

    $('changes').innerHTML = '';

    $('validation').textContent = '';

    $('summary').textContent = '';


    try {

        const response =
            await fetch(
                '/api/agent/run',
                {
                    method: 'POST',

                    headers: {
                        'Content-Type':
                            'application/json'
                    },

                    body: JSON.stringify({

                        task: task,

                        upload_id:
                            usingUpload
                                ? uploadedProjectId
                                : null
                    })
                }
            );


        const data =
            await response.json();


        if (!response.ok) {

            throw new Error(
                data.error ||
                'Agent failed'
            );
        }


        // ====================================
        // Agent mode
        // ====================================

        $('mode').textContent =
            data.demo_mode
                ? 'DEMO'
                : 'LIVE';


        // ====================================
        // Files scanned
        // ====================================

        $('scanned').textContent =
            `${data.files_scanned} files scanned`;


        // ====================================
        // Agent plan
        // ====================================

        $('plan').innerHTML =

            Array.isArray(data.plan) &&
            data.plan.length

                ? data.plan
                    .map(
                        (step, index) => `

                            <div class="step">

                                <b>${index + 1}</b>

                                <span>
                                    ${escapeHtml(step)}
                                </span>

                            </div>
                        `
                    )
                    .join('')

                : '<div class="empty">No plan returned.</div>';


        // ====================================
        // Relevant files
        // ====================================

        $('files').innerHTML =

            Array.isArray(data.relevant_files) &&
            data.relevant_files.length

                ? data.relevant_files
                    .map(
                        file => `

                            <div class="file">

                                <code>
                                    ${escapeHtml(file.path)}
                                </code>

                                <span>
                                    ${file.lines} lines
                                </span>

                            </div>
                        `
                    )
                    .join('')

                : '<div class="empty">No relevant files identified.</div>';


        // ====================================
        // Code changes
        // ====================================

        if (
            Array.isArray(data.changes) &&
            data.changes.length
        ) {

            $('changes').innerHTML =

                data.changes
                    .map(change => {

                        const diff =
                            change.diff ||
                            change.patch ||
                            '';

                        const summary =
                            change.summary ||
                            'Structured code change generated by the agent.';


                        return `

                            <div class="change">

                                <strong>
                                    ${escapeHtml(change.path)}
                                </strong>

                                <p>
                                    ${escapeHtml(summary)}
                                </p>

                                <div class="diff">
                                    ${escapeHtml(diff)}
                                </div>

                            </div>

                        `;

                    })
                    .join('');

        } else {

            $('changes').innerHTML =
                '<div class="empty">' +
                'No patch was generated in this run. ' +
                'The agent did not claim changes were applied.' +
                '</div>';
        }


        // ====================================
        // Validation
        // ====================================

        if (data.validation) {

            const skipped =
                data.validation.status === 'skipped';

            const passed =
                Boolean(data.validation.passed);

            const statusClass =
                skipped ? '' : (passed ? 'pass' : 'fail');

            const statusLabel =
                skipped
                    ? 'VALIDATION SKIPPED - SANDBOX REQUIRED'
                    : (passed
                        ? 'VALIDATION PASSED'
                        : 'VALIDATION FAILED');

            $('validation').innerHTML = `

                <span class="${statusClass}">

                    ${statusLabel}

                </span>

                <pre>
                    ${escapeHtml(
                        data.validation.output || ''
                    )}
                </pre>

            `;

        } else {

            $('validation').innerHTML =
                '<span class="fail">' +
                '✕ VALIDATION INFORMATION UNAVAILABLE' +
                '</span>';
        }


        // ====================================
        // Agent summary
        // ====================================

        $('summary').textContent =
            data.explanation ||
            'No explanation returned by the agent.';


    } catch (error) {

        $('error').textContent =
            error.message;

        $('error')
            .classList
            .remove('hidden');

    } finally {

        $('run').disabled = false;

        $('run').innerHTML =
            'Analyze & Build <span>→</span>';
    }
};


// ============================================
// HTML escaping
// ============================================

function escapeHtml(value) {

    return String(value).replace(
        /[&<>'"]/g,

        character => ({

            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            "'": '&#39;',
            '"': '&quot;'

        }[character])
    );
}
