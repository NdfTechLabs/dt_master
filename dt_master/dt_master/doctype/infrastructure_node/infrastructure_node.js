frappe.ui.form.on("Infrastructure Node", {
    refresh(frm) {
        if (frm.is_new()) return;

        const { status, state } = frm.doc;

        const actions = {
            install_dependencies: {
                statuses: ["Provisioning", "Maintenance"],
                states: ["SSH Ready","Error"],
                method: "dt_master.api.infrastructure_node.install_dependencies"
            },

            install_bench: {
                statuses: ["Provisioning", "Maintenance"],
                states: ["Deps Installed","Error"],
                method: "dt_master.api.infrastructure_node.install_bench"
            },

            initialize_project: {
                statuses: ["Provisioning", "Maintenance"],
                states: ["Project Installed","Error"],
                method: "dt_master.api.infrastructure_node.init_project"
            },

            prepare_project: {
                statuses: ["Provisioning", "Maintenance"],
                states: ["Initialized","Error"],
                method: "dt_master.api.infrastructure_node.prepare_production"
            },

            start_project: {
                statuses: ["Active", "Maintenance"],
                states: ["Healthy", "Stopped"],
                method: "dt_master.api.infrastructure_node.start_project"
            },

            stop_project: {
                statuses: ["Active", "Maintenance"],
                states: ["Running"],
                method: "dt_master.api.infrastructure_node.stop_project"
            },

            check_system_status: {
                statuses: ["Provisioning", "Active", "Maintenance"],
                states: ["SSH Ready", "Healthy", "Running", "Stopped"],
                method: "dt_master.api.infrastructure_node.verify_production"
            }
        };

        // 1. Hard reset UI
        Object.keys(actions).forEach(label => hide_button(frm, label));

        // 2. Evaluate lifecycle rules
        Object.entries(actions).forEach(([label, cfg]) => {
            const status_ok = cfg.statuses.includes(status);
            const state_ok  = cfg.states.includes(state);

            if (status_ok && state_ok) {
                show_and_bind(frm, label, cfg.method);
            }
        });

        if (frappe.user_roles.includes("System Manager") && ["Initialized", "Healthy", "Running", "Stopped", "Error"].includes(state)) {
            for (const [method, label] of Object.entries({
                backup_bench: "Backup Bench", update_bench: "Update Bench",
                migrate_bench: "Migrate Bench", remove_bench: "Remove Bench"
            })) {
                frm.add_custom_button(__(label), () => {
                    frappe.confirm(__(method === "remove_bench"
                        ? "Remove this bench and its virtualenv? Databases will be kept."
                        : "Run this bench operation? Updates and migrations take backups first."),
                        () => run_bench_operation(frm, method));
                }, __("Bench"));
            }
            frm.add_custom_button(__("Restore Backup"), () => {
                frappe.prompt([
                    {fieldname: "tenant_name", fieldtype: "Link", options: "Tenant Site", label: "Tenant Site", reqd: 1, get_query: () => ({filters: {node: frm.doc.name}})},
                    {fieldname: "restore_dir", fieldtype: "Data", label: "Absolute backup directory on node", reqd: 1}
                ], values => frappe.confirm(__("Replace the site's database and files with this backup?"),
                    () => run_bench_operation(frm, "restore_backup", values)), __("Restore Backup"));
            }, __("Bench"));
        }
    }
});

function run_bench_operation(frm, method, args = {}) {
    const dialog = create_log_dialog(method);
    start_execution_log_stream(dialog, frm.doc.name);
    frappe.call({
        method: `dt_master.api.infrastructure_node.${method}`,
        args: {node_name: frm.doc.name, ...args},
        callback: () => frm.reload_doc()
    });
}

function create_log_dialog(title) {

    const dialog = new frappe.ui.Dialog({
        title: `${title.replaceAll("_"," ")}`,
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "log_container"
            }
        ],
        size: "large"
    });

    dialog.show();

    dialog.log_el = dialog.fields_dict.log_container.$wrapper;

    dialog.log_el.css({
        "background": "#111",
        "color": "#0f0",
        "font-family": "monospace",
        "padding": "10px",
        "height": "400px",
        "overflow-y": "scroll"
    });

    // placeholder while waiting for first log
    dialog.log_el.append(`<div id="log-wait" style="color:#888">Starting remote script...</div>`);

    return dialog;
}

function show_and_bind(frm, label, method) {
    const btn = frm.get_field(label);
    if (!btn) return;

    btn.$wrapper.show();
    btn.$wrapper.off("click").on("click", () => {

        const dialog = create_log_dialog(label);

        // start listener first
        start_execution_log_stream(dialog, frm.doc.name);

        frappe.call({
            method,
            args: { node_name: frm.doc.name },

            callback() {
                frm.reload_doc();
            }
        });
    });
}

function start_execution_log_stream(dialog, node_name) {

    let first_line_received = false;

    frappe.realtime.off("script_execution_log");
    frappe.realtime.on("script_execution_log", data => {
        if (data.node !== node_name) return;

        if (data.line) {

            if (!first_line_received) {
                dialog.log_el.find("#log-wait").remove();
                first_line_received = true;
            }

            dialog.log_el.append(
                `<div>${frappe.utils.escape_html(data.line)}</div>`
            );

            dialog.log_el.scrollTop(dialog.log_el[0].scrollHeight);
        }

        if (data.status === "Completed") {
            dialog.log_el.append(`<div style="color:#0f0">PROCESS COMPLETED</div>`);
        }

        if (data.status === "Failed") {
            dialog.log_el.append(`<div style="color:red">PROCESS FAILED</div>`);
        }

    });
}

function hide_button(frm, label) {
    const btn = frm.get_field(label);
    if (btn) btn.$wrapper.hide();
}
