/**
 * OpenInstaFlow Dashboard — Main Application
 * 
 * Single-page app with hash-based routing.
 * Renders both Admin dashboard and Customer portal.
 */

const App = {
    currentPage: null,

    // ── Initialization ────────────────────────────────────────────────
    init() {
        window.addEventListener('hashchange', () => this.route());
        this.route();
    },

    // ── Router ────────────────────────────────────────────────────────
    route() {
        const rawHash = window.location.hash || '#/login';
        const [hash, hashQuery] = rawHash.split('?');
        this.hashQuery = hashQuery || '';
        const [path, ...params] = hash.slice(2).split('/');

        if (!API.isLoggedIn() && path !== 'login' && path !== 'signup') {
            window.location.hash = '#/login';
            return;
        }

        if (API.isLoggedIn() && (path === 'login' || path === 'signup')) {
            window.location.hash = API.isAdmin() ? '#/dashboard' : '#/my-dashboard';
            return;
        }

        const root = document.getElementById('app');
        root.innerHTML = '';

        // Admin routes
        if (API.isAdmin()) {
            switch (path) {
                case 'dashboard': return this.renderAdminDashboard(root);
                case 'customers': return this.renderCustomersList(root);
                case 'customer': return this.renderCustomerDetail(root, params[0]);
                case 'codes': return this.renderActivationCodes(root);
                case 'posts': return this.renderAllPosts(root);
                case 'activity': return this.renderActivity(root);
                default: window.location.hash = '#/dashboard';
            }
        }
        // Customer routes
        else {
            switch (path) {
                case 'my-dashboard': return this.renderMyDashboard(root);
                case 'my-posts': return this.renderMyPosts(root);
                case 'my-publish': return this.renderMyPublish(root);
                case 'my-autopilot': return this.renderMyAutopilot(root);
                case 'my-settings': return this.renderMySettings(root);
                default: window.location.hash = '#/my-dashboard';
            }
        }
    },

    // ── Layout wrapper ────────────────────────────────────────────────
    withLayout(root, activePage, pageTitle, headerActions, bodyContent) {
        const isAdmin = API.isAdmin();
        const user = API.getUserData();
        const userName = user?.name || user?.email || 'User';
        const initial = userName.charAt(0).toUpperCase();

        const navItems = isAdmin ? `
            <button class="nav-item ${activePage === 'dashboard' ? 'active' : ''}" onclick="location.hash='#/dashboard'">
                <span class="nav-icon">📊</span> Overview
            </button>
            <button class="nav-item ${activePage === 'customers' ? 'active' : ''}" onclick="location.hash='#/customers'">
                <span class="nav-icon">👥</span> Customers
            </button>
            <button class="nav-item ${activePage === 'posts' ? 'active' : ''}" onclick="location.hash='#/posts'">
                <span class="nav-icon">📸</span> All Posts
            </button>
            <button class="nav-item ${activePage === 'codes' ? 'active' : ''}" onclick="location.hash='#/codes'">
                <span class="nav-icon">🔑</span> Activation Codes
            </button>
            <button class="nav-item ${activePage === 'activity' ? 'active' : ''}" onclick="location.hash='#/activity'">
                <span class="nav-icon">📋</span> Activity Log
            </button>
        ` : `
            <button class="nav-item ${activePage === 'my-dashboard' ? 'active' : ''}" onclick="location.hash='#/my-dashboard'">
                <span class="nav-icon">📊</span> Dashboard
            </button>
            <button class="nav-item ${activePage === 'my-posts' ? 'active' : ''}" onclick="location.hash='#/my-posts'">
                <span class="nav-icon">📸</span> My Posts
            </button>
            <button class="nav-item ${activePage === 'my-publish' ? 'active' : ''}" onclick="location.hash='#/my-publish'">
                <span class="nav-icon">🚀</span> Publish / Schedule
            </button>
            <button class="nav-item ${activePage === 'my-autopilot' ? 'active' : ''}" onclick="location.hash='#/my-autopilot'">
                <span class="nav-icon">🤖</span> Autopilot
            </button>
            <button class="nav-item ${activePage === 'my-settings' ? 'active' : ''}" onclick="location.hash='#/my-settings'">
                <span class="nav-icon">⚙️</span> Settings
            </button>
        `;

        root.innerHTML = `
            <div class="app-layout">
                <aside class="sidebar" id="sidebar">
                    <div class="sidebar-brand">
                        <h2>OpenInstaFlow</h2>
                        <small>${isAdmin ? 'Admin Dashboard' : 'Customer Portal'}</small>
                    </div>
                    <nav class="sidebar-nav">
                        ${navItems}
                    </nav>
                    <div class="sidebar-footer">
                        <div class="sidebar-user">
                            <div class="avatar">${UI.esc(initial)}</div>
                            <div class="user-info">
                                <div class="user-name">${UI.esc(userName)}</div>
                                <div class="user-role">${UI.esc(isAdmin ? 'admin' : 'customer')}</div>
                            </div>
                        </div>
                        <button class="nav-item mt-1" onclick="App.logout()" style="color: var(--accent-red);">
                            <span class="nav-icon">🚪</span> Logout
                        </button>
                    </div>
                </aside>
                <main class="main-content">
                    <div class="page-header">
                        <h1>${UI.esc(pageTitle)}</h1>
                        <div class="header-actions">${headerActions || ''}</div>
                    </div>
                    <div class="page-body" id="page-body">
                        ${bodyContent || UI.loading()}
                    </div>
                </main>
            </div>
        `;
    },

    logout() {
        API.clearAuth();
        window.location.hash = '#/login';
    },

    // ══════════════════════════════════════════════════════════════════
    // LOGIN PAGE
    // ══════════════════════════════════════════════════════════════════

    renderLoginPage(root) {
        const hash = window.location.hash;
        const isSignup = hash === '#/signup';

        root.innerHTML = `
            <div class="login-container">
                <div class="login-card">
                    <div class="logo">
                        <h1>OpenInstaFlow</h1>
                        <p>Instagram automation platform</p>
                    </div>
                    <div class="login-tabs">
                        <button class="login-tab ${!isSignup ? 'active' : ''}" onclick="location.hash='#/login'">Log In</button>
                        <button class="login-tab ${isSignup ? 'active' : ''}" onclick="location.hash='#/signup'">Sign Up</button>
                    </div>
                    ${isSignup ? this._signupForm() : this._loginForm()}
                </div>
            </div>
        `;
    },

    _loginForm() {
        return `
            <form id="login-form" onsubmit="App.handleLogin(event)">
                <div class="login-tabs" style="margin-bottom: 1.5rem;">
                    <button type="button" class="login-tab active" id="tab-admin" onclick="App.switchLoginType('admin')">Admin</button>
                    <button type="button" class="login-tab" id="tab-customer" onclick="App.switchLoginType('customer')">Customer</button>
                </div>
                <input type="hidden" id="login-type" value="admin">
                <div class="form-group">
                    <label>Email</label>
                    <input class="form-input" type="email" id="login-email" placeholder="you@example.com" required>
                </div>
                <div class="form-group">
                    <label>Password</label>
                    <input class="form-input" type="password" id="login-password" placeholder="••••••••" required>
                </div>
                <button class="btn btn-primary btn-block mt-2" type="submit" id="login-btn">Log In</button>
            </form>
        `;
    },

    _signupForm() {
        return `
            <form id="signup-form" onsubmit="App.handleSignup(event)">
                <div class="form-group">
                    <label>Full Name</label>
                    <input class="form-input" type="text" id="signup-name" placeholder="John Doe" required>
                </div>
                <div class="form-group">
                    <label>Email</label>
                    <input class="form-input" type="email" id="signup-email" placeholder="you@example.com" required>
                </div>
                <div class="form-group">
                    <label>Password</label>
                    <input class="form-input" type="password" id="signup-password" placeholder="Min 6 characters" required minlength="6">
                </div>
                <div class="form-group">
                    <label>Activation Code</label>
                    <input class="form-input" type="text" id="signup-code" placeholder="Paste your activation code" required>
                </div>
                <button class="btn btn-primary btn-block mt-2" type="submit" id="signup-btn">Create Account</button>
            </form>
        `;
    },

    switchLoginType(type) {
        document.getElementById('login-type').value = type;
        document.getElementById('tab-admin').className = `login-tab ${type === 'admin' ? 'active' : ''}`;
        document.getElementById('tab-customer').className = `login-tab ${type === 'customer' ? 'active' : ''}`;
    },

    async handleLogin(e) {
        e.preventDefault();
        const btn = document.getElementById('login-btn');
        btn.disabled = true;
        btn.textContent = 'Logging in...';

        try {
            const type = document.getElementById('login-type').value;
            const email = document.getElementById('login-email').value;
            const password = document.getElementById('login-password').value;
            const data = type === 'admin'
                ? await API.adminLogin(email, password)
                : await API.customerLogin(email, password);
            API.saveAuth(data);
            UI.toast('Welcome back!', 'success');
            window.location.hash = data.role === 'admin' ? '#/dashboard' : '#/my-dashboard';
        } catch (err) {
            UI.toast(err.message, 'error');
            btn.disabled = false;
            btn.textContent = 'Log In';
        }
    },

    async handleSignup(e) {
        e.preventDefault();
        const btn = document.getElementById('signup-btn');
        btn.disabled = true;
        btn.textContent = 'Creating account...';

        try {
            const data = await API.customerSignup(
                document.getElementById('signup-email').value,
                document.getElementById('signup-password').value,
                document.getElementById('signup-name').value,
                document.getElementById('signup-code').value
            );
            API.saveAuth(data);
            UI.toast('Account created! Welcome!', 'success');
            window.location.hash = '#/my-settings';
        } catch (err) {
            UI.toast(err.message, 'error');
            btn.disabled = false;
            btn.textContent = 'Create Account';
        }
    },

    // ══════════════════════════════════════════════════════════════════
    // ADMIN: Dashboard Overview
    // ══════════════════════════════════════════════════════════════════

    async renderAdminDashboard(root) {
        this.withLayout(root, 'dashboard', 'Overview', '', UI.loading());

        try {
            const [stats, actData] = await Promise.all([
                API.getDashboardStats(),
                API.getActivity(10),
            ]);

            const body = document.getElementById('page-body');
            body.innerHTML = `
                <div class="stats-grid">
                    ${UI.statCard('👥', stats.active_customers, 'Active Customers')}
                    ${UI.statCard('📸', stats.total_posts, 'Total Posts')}
                    ${UI.statCard('✅', stats.published_posts, 'Published', 'accent-green')}
                    ${UI.statCard('⏳', stats.pending_posts, 'Scheduled', 'accent-amber')}
                    ${UI.statCard('❌', stats.failed_posts, 'Failed', 'accent-red')}
                    ${UI.statCard('📊', stats.success_rate + '%', 'Success Rate', 'accent-green')}
                    ${UI.statCard('📅', stats.posts_today, 'Posts Today')}
                    ${UI.statCard('🔑', stats.unused_codes, 'Unused Codes')}
                </div>

                <div class="card mt-2">
                    <div class="card-header">
                        <h3>Recent Activity</h3>
                        <button class="btn btn-sm btn-secondary" onclick="location.hash='#/activity'">View All</button>
                    </div>
                    <div class="activity-feed">
                        ${actData.activity.length === 0
                            ? UI.emptyState('📋', 'No activity yet', 'Activity will appear here as you and your customers use the platform.')
                            : actData.activity.map(a => `
                                <div class="activity-item">
                                    <div class="activity-dot"></div>
                                    <div class="activity-content">
                                        <div class="activity-action">${UI.esc(a.action.replace(/_/g, ' '))}</div>
                                        ${a.customer_name ? `<div class="activity-details">${UI.esc(a.customer_name)}</div>` : ''}
                                        ${a.details ? `<div class="activity-details">${UI.esc(a.details.substring(0, 100))}</div>` : ''}
                                        <div class="activity-time">${UI.timeAgo(a.timestamp)}</div>
                                    </div>
                                </div>
                            `).join('')
                        }
                    </div>
                </div>
            `;
        } catch (err) {
            document.getElementById('page-body').innerHTML = `<p style="color:var(--accent-red);">Error: ${UI.esc(err.message)}</p>`;
        }
    },

    // ══════════════════════════════════════════════════════════════════
    // ADMIN: Customers List
    // ══════════════════════════════════════════════════════════════════

    async renderCustomersList(root) {
        this.withLayout(root, 'customers', 'Customers', '', UI.loading());

        try {
            const data = await API.getCustomers();
            const customers = data.customers;

            const body = document.getElementById('page-body');
            if (customers.length === 0) {
                body.innerHTML = UI.emptyState('👥', 'No customers yet', 'Generate activation codes and share them with your customers to get started.',
                    '<button class="btn btn-primary" onclick="location.hash=\'#/codes\'">Generate Codes</button>');
                return;
            }

            body.innerHTML = `
                <div class="data-table-container">
                    <table class="data-table">
                        <thead>
                            <tr>
                                <th>Customer</th>
                                <th>IG Username</th>
                                <th>Status</th>
                                <th>Posts</th>
                                <th>Joined</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${customers.map(c => `
                                <tr>
                                    <td>
                                        <strong>${UI.esc(c.name)}</strong>
                                        <div class="text-xs text-muted">${UI.esc(c.email)}</div>
                                    </td>
                                    <td>${c.ig_username ? '@' + UI.esc(c.ig_username) : '<span class="text-muted">Not set</span>'}</td>
                                    <td>${UI.badge(c.status)}</td>
                                    <td>${c.post_count || 0}</td>
                                    <td><span class="text-sm">${UI.timeAgo(c.created_at)}</span></td>
                                    <td>
                                        <div class="flex gap-1">
                                            <button class="btn btn-sm btn-secondary" onclick="location.hash='#/customer/${c.id}'">View</button>
                                            <button class="btn btn-sm btn-secondary" onclick="App.testToken('${c.id}')">Test</button>
                                        </div>
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            `;
        } catch (err) {
            document.getElementById('page-body').innerHTML = `<p style="color:var(--accent-red);">Error: ${UI.esc(err.message)}</p>`;
        }
    },

    async testToken(customerId) {
        UI.toast('Testing token...', 'info');
        try {
            const result = await API.testCustomerToken(customerId);
            if (result.status === 'ok') {
                UI.toast(`Token valid! @${result.profile.username}`, 'success');
            } else {
                UI.toast(`Token error: ${result.message}`, 'error');
            }
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    // ══════════════════════════════════════════════════════════════════
    // ADMIN: Customer Detail
    // ══════════════════════════════════════════════════════════════════

    async renderCustomerDetail(root, customerId) {
        this.withLayout(root, 'customers', 'Customer Details', '', UI.loading());

        try {
            const [custData, postsData] = await Promise.all([
                API.getCustomer(customerId),
                API.getCustomerPosts(customerId),
            ]);
            const c = custData.customer;
            const posts = postsData.posts;

            const body = document.getElementById('page-body');
            body.innerHTML = `
                <div class="card mb-3 slide-up">
                    <div class="card-header">
                        <h3>${UI.esc(c.name)}</h3>
                        <div class="flex gap-1">
                            ${UI.badge(c.status)}
                            <button class="btn btn-sm btn-secondary" onclick="App.editCustomerModal('${c.id}')">Edit</button>
                            <button class="btn btn-sm btn-danger" onclick="App.deleteCustomer('${c.id}')">Delete</button>
                        </div>
                    </div>
                    <div class="card-body">
                        <div class="detail-grid">
                            <div class="detail-field">
                                <span class="detail-label">Email</span>
                                <span class="detail-value">${UI.esc(c.email)}</span>
                            </div>
                            <div class="detail-field">
                                <span class="detail-label">IG Username</span>
                                <span class="detail-value">${c.ig_username ? '@' + UI.esc(c.ig_username) : '—'}</span>
                            </div>
                            <div class="detail-field">
                                <span class="detail-label">IG User ID</span>
                                <span class="detail-value">${UI.esc(c.ig_user_id) || '—'}</span>
                            </div>
                            <div class="detail-field">
                                <span class="detail-label">Login Kind</span>
                                <span class="detail-value">${UI.esc(c.login_kind)}</span>
                            </div>
                            <div class="detail-field">
                                <span class="detail-label">Token</span>
                                <span class="detail-value">${c.ig_access_token ? '••••••••' + c.ig_access_token.slice(-8) : '—'}</span>
                            </div>
                            <div class="detail-field">
                                <span class="detail-label">Joined</span>
                                <span class="detail-value">${UI.formatDate(c.created_at)}</span>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="card slide-up">
                    <div class="card-header">
                        <h3>Post History (${posts.length})</h3>
                    </div>
                    ${posts.length === 0
                        ? `<div class="card-body">${UI.emptyState('📸', 'No posts yet', 'Posts will appear here once they start publishing.')}</div>`
                        : `<table class="data-table">
                            <thead>
                                <tr>
                                    <th>Type</th>
                                    <th>Caption</th>
                                    <th>Status</th>
                                    <th>Date</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${posts.map(p => `
                                    <tr>
                                        <td>${UI.esc(p.media_type)}</td>
                                        <td class="truncate" style="max-width:300px">${UI.esc(p.caption || '—')}</td>
                                        <td>${UI.badge(p.status)}</td>
                                        <td><span class="text-sm">${UI.timeAgo(p.published_at || p.scheduled_time || p.created_at)}</span></td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>`
                    }
                </div>
            `;
        } catch (err) {
            document.getElementById('page-body').innerHTML = `<p style="color:var(--accent-red);">Error: ${UI.esc(err.message)}</p>`;
        }
    },

    editCustomerModal(customerId) {
        API.getCustomer(customerId).then(data => {
            const c = data.customer;
            UI.showModal('Edit Customer', `
                <form id="edit-customer-form">
                    <div class="form-group">
                        <label>Name</label>
                        <input class="form-input" id="ec-name" value="${UI.esc(c.name)}">
                    </div>
                    <div class="form-group">
                        <label>IG Username</label>
                        <input class="form-input" id="ec-ig-username" value="${UI.esc(c.ig_username || '')}">
                    </div>
                    <div class="form-group">
                        <label>IG User ID</label>
                        <input class="form-input" id="ec-ig-user-id" value="${UI.esc(c.ig_user_id || '')}">
                    </div>
                    <div class="form-group">
                        <label>Access Token</label>
                        <input class="form-input" id="ec-token" placeholder="Leave blank to keep current">
                    </div>
                    <div class="form-group">
                        <label>Status</label>
                        <select class="form-input" id="ec-status">
                            <option value="active" ${c.status === 'active' ? 'selected' : ''}>Active</option>
                            <option value="paused" ${c.status === 'paused' ? 'selected' : ''}>Paused</option>
                            <option value="expired" ${c.status === 'expired' ? 'selected' : ''}>Expired</option>
                        </select>
                    </div>
                </form>
            `, `
                <button class="btn btn-secondary" onclick="UI.closeModal()">Cancel</button>
                <button class="btn btn-primary" onclick="App.saveCustomerEdit('${customerId}')">Save Changes</button>
            `);
        });
    },

    async saveCustomerEdit(customerId) {
        const updates = {};
        const name = document.getElementById('ec-name')?.value;
        const igUsername = document.getElementById('ec-ig-username')?.value;
        const igUserId = document.getElementById('ec-ig-user-id')?.value;
        const token = document.getElementById('ec-token')?.value;
        const status = document.getElementById('ec-status')?.value;

        if (name) updates.name = name;
        if (igUsername !== undefined) updates.ig_username = igUsername;
        if (igUserId !== undefined) updates.ig_user_id = igUserId;
        if (token) updates.ig_access_token = token;
        if (status) updates.status = status;

        try {
            await API.updateCustomer(customerId, updates);
            UI.closeModal();
            UI.toast('Customer updated!', 'success');
            this.route(); // refresh
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    async deleteCustomer(customerId) {
        const ok = await UI.confirm('Delete Customer', 'This will permanently delete this customer and all their data. Are you sure?');
        if (!ok) return;
        try {
            await API.deleteCustomer(customerId);
            UI.toast('Customer deleted.', 'success');
            window.location.hash = '#/customers';
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    // ══════════════════════════════════════════════════════════════════
    // ADMIN: Activation Codes
    // ══════════════════════════════════════════════════════════════════

    async renderActivationCodes(root) {
        this.withLayout(root, 'codes', 'Activation Codes',
            '<button class="btn btn-primary btn-sm" onclick="App.generateCodes()">+ Generate Codes</button>',
            UI.loading());

        try {
            const data = await API.getActivationCodes();
            const codes = data.codes;
            const body = document.getElementById('page-body');

            const unused = codes.filter(c => !c.is_redeemed);
            const used = codes.filter(c => c.is_redeemed);

            body.innerHTML = `
                <div class="stats-grid mb-3">
                    ${UI.statCard('🔑', codes.length, 'Total Codes')}
                    ${UI.statCard('✨', unused.length, 'Available', 'accent-green')}
                    ${UI.statCard('✅', used.length, 'Redeemed')}
                </div>

                ${unused.length > 0 ? `
                <div class="card mb-3">
                    <div class="card-header">
                        <h3>Available Codes</h3>
                    </div>
                    <table class="data-table">
                        <thead><tr><th>Code</th><th>Created</th><th>Actions</th></tr></thead>
                        <tbody>
                            ${unused.map(c => `
                                <tr>
                                    <td><span class="code-display" onclick="UI.copyToClipboard('${c.code}')" title="Click to copy">${UI.esc(c.code)}</span></td>
                                    <td class="text-sm">${UI.timeAgo(c.created_at)}</td>
                                    <td>
                                        <div class="flex gap-1">
                                            <button class="btn btn-sm btn-secondary" onclick="UI.copyToClipboard('${c.code}')">Copy</button>
                                            <button class="btn btn-sm btn-danger" onclick="App.deleteCode('${c.id}')">Delete</button>
                                        </div>
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
                ` : ''}

                ${used.length > 0 ? `
                <div class="card">
                    <div class="card-header">
                        <h3>Redeemed Codes</h3>
                    </div>
                    <table class="data-table">
                        <thead><tr><th>Code</th><th>Customer</th><th>Redeemed</th></tr></thead>
                        <tbody>
                            ${used.map(c => `
                                <tr>
                                    <td class="text-muted">${UI.esc(c.code)}</td>
                                    <td>${UI.esc(c.customer_name || c.customer_email || '—')}</td>
                                    <td class="text-sm">${UI.timeAgo(c.redeemed_at)}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
                ` : ''}
            `;
        } catch (err) {
            document.getElementById('page-body').innerHTML = `<p style="color:var(--accent-red);">Error: ${UI.esc(err.message)}</p>`;
        }
    },

    async generateCodes() {
        UI.showModal('Generate Activation Codes', `
            <div class="form-group">
                <label>How many codes?</label>
                <input class="form-input" type="number" id="gen-count" value="5" min="1" max="50">
            </div>
        `, `
            <button class="btn btn-secondary" onclick="UI.closeModal()">Cancel</button>
            <button class="btn btn-primary" onclick="App.doGenerateCodes()">Generate</button>
        `);
    },

    async doGenerateCodes() {
        const count = parseInt(document.getElementById('gen-count')?.value) || 5;
        try {
            const data = await API.generateActivationCodes(count);
            UI.closeModal();
            UI.toast(`Generated ${data.codes.length} activation codes!`, 'success');
            this.route();
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    async deleteCode(codeId) {
        try {
            await API.deleteActivationCode(codeId);
            UI.toast('Code deleted.', 'success');
            this.route();
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    // ══════════════════════════════════════════════════════════════════
    // ADMIN: All Posts
    // ══════════════════════════════════════════════════════════════════

    async renderAllPosts(root) {
        this.withLayout(root, 'posts', 'All Posts',
            `<select class="form-input" style="width:auto;" id="post-filter" onchange="App.filterPosts()">
                <option value="">All Statuses</option>
                <option value="published">Published</option>
                <option value="pending">Pending</option>
                <option value="failed">Failed</option>
                <option value="cancelled">Cancelled</option>
            </select>`,
            UI.loading());

        await this._loadPosts();
    },

    async _loadPosts(status) {
        try {
            const data = await API.getAllPosts(status);
            const posts = data.posts;
            const body = document.getElementById('page-body');

            if (posts.length === 0) {
                body.innerHTML = UI.emptyState('📸', 'No posts found', 'Posts will appear here once customers start publishing.');
                return;
            }

            body.innerHTML = `
                <div class="data-table-container">
                    <table class="data-table">
                        <thead>
                            <tr>
                                <th>Customer</th>
                                <th>Type</th>
                                <th>Caption</th>
                                <th>Status</th>
                                <th>Scheduled</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${posts.map(p => `
                                <tr>
                                    <td>${UI.esc(p.customer_name || '—')}</td>
                                    <td>${UI.esc(p.media_type)}</td>
                                    <td class="truncate" style="max-width:250px">${UI.esc(p.caption || '—')}</td>
                                    <td>${UI.badge(p.status)}</td>
                                    <td class="text-sm">${p.scheduled_time ? UI.formatDate(p.scheduled_time) : UI.timeAgo(p.created_at)}</td>
                                    <td>
                                        ${p.status === 'pending' ? `<button class="btn btn-sm btn-danger" onclick="App.cancelPostAdmin('${p.id}')">Cancel</button>` : ''}
                                        ${p.permalink ? `<a href="${p.permalink}" target="_blank" class="btn btn-sm btn-secondary">View ↗</a>` : ''}
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            `;
        } catch (err) {
            document.getElementById('page-body').innerHTML = `<p style="color:var(--accent-red);">Error: ${UI.esc(err.message)}</p>`;
        }
    },

    filterPosts() {
        const filter = document.getElementById('post-filter')?.value;
        this._loadPosts(filter || undefined);
    },

    async cancelPostAdmin(postId) {
        try {
            await API.cancelPost(postId);
            UI.toast('Post cancelled.', 'success');
            this.filterPosts();
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    // ══════════════════════════════════════════════════════════════════
    // ADMIN: Activity Log
    // ══════════════════════════════════════════════════════════════════

    async renderActivity(root) {
        this.withLayout(root, 'activity', 'Activity Log', '', UI.loading());

        try {
            const data = await API.getActivity(100);
            const body = document.getElementById('page-body');

            if (data.activity.length === 0) {
                body.innerHTML = UI.emptyState('📋', 'No activity yet', 'Activity will appear here as actions occur.');
                return;
            }

            body.innerHTML = `
                <div class="card">
                    <div class="activity-feed">
                        ${data.activity.map(a => `
                            <div class="activity-item">
                                <div class="activity-dot"></div>
                                <div class="activity-content">
                                    <div class="activity-action">${UI.esc(a.action.replace(/_/g, ' '))}</div>
                                    ${a.customer_name ? `<div class="activity-details"><strong>${UI.esc(a.customer_name)}</strong></div>` : ''}
                                    ${a.details ? `<div class="activity-details">${UI.esc(a.details)}</div>` : ''}
                                    <div class="activity-time">${UI.formatDate(a.timestamp)}</div>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        } catch (err) {
            document.getElementById('page-body').innerHTML = `<p style="color:var(--accent-red);">Error: ${UI.esc(err.message)}</p>`;
        }
    },

    // ══════════════════════════════════════════════════════════════════
    // CUSTOMER: Dashboard
    // ══════════════════════════════════════════════════════════════════

    async renderMyDashboard(root) {
        this.withLayout(root, 'my-dashboard', 'Dashboard', '', UI.loading());

        try {
            const [profile, posts, activity] = await Promise.all([
                API.getMyProfile(),
                API.getMyPosts(10),
                API.getMyActivity(5),
            ]);
            const c = profile.customer;
            const recentPosts = posts.posts;

            const published = recentPosts.filter(p => p.status === 'published').length;
            const pending = recentPosts.filter(p => p.status === 'pending').length;

            const body = document.getElementById('page-body');
            body.innerHTML = `
                ${!c.ig_user_id ? `
                    <div class="card mb-3 slide-up" style="border-color: var(--accent-amber);">
                        <div class="card-body" style="display:flex; align-items:center; gap:1rem;">
                            <span style="font-size:2rem;">⚠️</span>
                            <div>
                                <h3>Setup Required</h3>
                                <p class="text-sm text-muted mt-1">You haven't connected your Instagram account yet. Go to Settings to add your credentials.</p>
                                <button class="btn btn-primary btn-sm mt-1" onclick="location.hash='#/my-settings'">Go to Settings</button>
                            </div>
                        </div>
                    </div>
                ` : ''}

                <div class="stats-grid">
                    ${UI.statCard('📸', recentPosts.length, 'Recent Posts')}
                    ${UI.statCard('✅', published, 'Published', 'accent-green')}
                    ${UI.statCard('⏳', pending, 'Scheduled', 'accent-amber')}
                    ${UI.statCard('👤', c.ig_username ? '@' + c.ig_username : 'Not set', 'Instagram')}
                </div>

                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:1.5rem;" class="mt-2">
                    <div class="card slide-up">
                        <div class="card-header">
                            <h3>Recent Posts</h3>
                            <button class="btn btn-sm btn-secondary" onclick="location.hash='#/my-posts'">View All</button>
                        </div>
                        ${recentPosts.length === 0
                            ? `<div class="card-body">${UI.emptyState('📸', 'No posts yet', 'Publish your first post!')}</div>`
                            : `<table class="data-table">
                                <thead><tr><th>Type</th><th>Status</th><th>Date</th></tr></thead>
                                <tbody>
                                    ${recentPosts.slice(0, 5).map(p => `
                                        <tr>
                                            <td>${UI.esc(p.media_type)}</td>
                                            <td>${UI.badge(p.status)}</td>
                                            <td class="text-sm">${UI.timeAgo(p.published_at || p.created_at)}</td>
                                        </tr>
                                    `).join('')}
                                </tbody>
                            </table>`
                        }
                    </div>

                    <div class="card slide-up">
                        <div class="card-header">
                            <h3>Activity</h3>
                        </div>
                        <div class="activity-feed">
                            ${activity.activity.length === 0
                                ? UI.emptyState('📋', 'No activity', '')
                                : activity.activity.map(a => `
                                    <div class="activity-item">
                                        <div class="activity-dot"></div>
                                        <div class="activity-content">
                                            <div class="activity-action">${UI.esc(a.action.replace(/_/g, ' '))}</div>
                                            <div class="activity-time">${UI.timeAgo(a.timestamp)}</div>
                                        </div>
                                    </div>
                                `).join('')
                            }
                        </div>
                    </div>
                </div>
            `;
        } catch (err) {
            document.getElementById('page-body').innerHTML = `<p style="color:var(--accent-red);">Error: ${UI.esc(err.message)}</p>`;
        }
    },

    // ══════════════════════════════════════════════════════════════════
    // CUSTOMER: My Posts
    // ══════════════════════════════════════════════════════════════════

    async renderMyPosts(root) {
        this.withLayout(root, 'my-posts', 'My Posts',
            '<button class="btn btn-primary btn-sm" onclick="location.hash=\'#/my-publish\'">+ New Post</button>',
            UI.loading());

        try {
            const data = await API.getMyPosts();
            const posts = data.posts;
            const body = document.getElementById('page-body');

            if (posts.length === 0) {
                body.innerHTML = UI.emptyState('📸', 'No posts yet', 'Publish your first post to Instagram!',
                    '<button class="btn btn-primary" onclick="location.hash=\'#/my-publish\'">Create Post</button>');
                return;
            }

            body.innerHTML = `
                <div class="data-table-container">
                    <table class="data-table">
                        <thead>
                            <tr>
                                <th>Type</th>
                                <th>Caption</th>
                                <th>Status</th>
                                <th>Date</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${posts.map(p => `
                                <tr>
                                    <td>${UI.esc(p.media_type)}</td>
                                    <td class="truncate" style="max-width:300px">${UI.esc(p.caption || '—')}</td>
                                    <td>${UI.badge(p.status)}</td>
                                    <td class="text-sm">${UI.formatDate(p.published_at || p.scheduled_time || p.created_at)}</td>
                                    <td>
                                        ${p.status === 'pending' ? `<button class="btn btn-sm btn-danger" onclick="App.cancelMyPost('${p.id}')">Cancel</button>` : ''}
                                        ${p.permalink ? `<a href="${p.permalink}" target="_blank" class="btn btn-sm btn-secondary">View ↗</a>` : ''}
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            `;
        } catch (err) {
            document.getElementById('page-body').innerHTML = `<p style="color:var(--accent-red);">Error: ${UI.esc(err.message)}</p>`;
        }
    },

    async cancelMyPost(postId) {
        try {
            await API.cancelMyPost(postId);
            UI.toast('Post cancelled.', 'success');
            this.route();
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    // ══════════════════════════════════════════════════════════════════
    // CUSTOMER: Publish / Schedule
    // ══════════════════════════════════════════════════════════════════

    renderMyPublish(root) {
        this.withLayout(root, 'my-publish', 'Publish / Schedule', '', '');

        const body = document.getElementById('page-body');
        body.innerHTML = `
            <div style="max-width: 600px;">
                <div class="card slide-up">
                    <div class="card-header">
                        <h3>🚀 Create a Post</h3>
                    </div>
                    <div class="card-body">
                        <form id="publish-form" onsubmit="App.handlePublish(event)">
                            <div class="form-group">
                                <label>Media Type</label>
                                <select class="form-input" id="pub-media-type">
                                    <option value="image">Image</option>
                                    <option value="reel">Reel / Video</option>
                                    <option value="story">Story</option>
                                </select>
                            </div>

                            <div class="form-group" id="pub-image-group">
                                <label>Image URL</label>
                                <input class="form-input" type="url" id="pub-image-url" placeholder="https://example.com/photo.jpg">
                            </div>

                            <div class="form-group hidden" id="pub-video-group">
                                <label>Video URL</label>
                                <input class="form-input" type="url" id="pub-video-url" placeholder="https://example.com/video.mp4">
                            </div>

                            <div class="form-group">
                                <label>Caption</label>
                                <textarea class="form-input" id="pub-caption" placeholder="Write your caption... #hashtags"></textarea>
                            </div>

                            <div class="form-group">
                                <label>Schedule (optional — leave blank to publish now)</label>
                                <input class="form-input" type="datetime-local" id="pub-schedule">
                            </div>

                            <div class="flex gap-1 mt-2">
                                <button class="btn btn-primary" type="submit" id="pub-btn">Publish Now</button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        `;

        // Toggle image/video URL visibility
        document.getElementById('pub-media-type').addEventListener('change', (e) => {
            const isVideo = e.target.value === 'reel';
            document.getElementById('pub-image-group').classList.toggle('hidden', isVideo);
            document.getElementById('pub-video-group').classList.toggle('hidden', !isVideo);
        });

        // Update button text based on schedule
        document.getElementById('pub-schedule').addEventListener('change', (e) => {
            document.getElementById('pub-btn').textContent = e.target.value ? 'Schedule Post' : 'Publish Now';
        });
    },

    async handlePublish(e) {
        e.preventDefault();
        const btn = document.getElementById('pub-btn');
        btn.disabled = true;
        const origText = btn.textContent;
        btn.textContent = 'Processing...';

        try {
            const mediaType = document.getElementById('pub-media-type').value;
            const imageUrl = document.getElementById('pub-image-url').value;
            const videoUrl = document.getElementById('pub-video-url').value;
            const caption = document.getElementById('pub-caption').value;
            const scheduleTime = document.getElementById('pub-schedule').value;

            if (scheduleTime) {
                // Schedule for later
                const isoTime = new Date(scheduleTime).toISOString();
                const data = {
                    scheduled_time: isoTime,
                    caption: caption || undefined,
                    media_type: mediaType,
                };
                if (mediaType === 'reel') data.video_url = videoUrl;
                else data.image_url = imageUrl;

                await API.scheduleMyPost(data);
                UI.toast('Post scheduled!', 'success');
            } else {
                // Publish now
                const data = {
                    caption: caption || undefined,
                    media_type: mediaType,
                };
                if (mediaType === 'reel') data.video_url = videoUrl;
                else data.image_url = imageUrl;

                await API.publishMyPost(data);
                UI.toast('Post published!', 'success');
            }
            window.location.hash = '#/my-posts';
        } catch (err) {
            UI.toast(err.message, 'error');
            btn.disabled = false;
            btn.textContent = origText;
        }
    },

    // ══════════════════════════════════════════════════════════════════
    // CUSTOMER: Autopilot (settings, Google Drive link, media queue)
    // ══════════════════════════════════════════════════════════════════

    async renderMyAutopilot(root) {
        this.withLayout(root, 'my-autopilot', 'Autopilot', '', UI.loading());

        const params = new URLSearchParams(this.hashQuery || '');
        const gdriveParam = params.get('gdrive');
        if (gdriveParam === 'connected') UI.toast('Google Drive connected!', 'success');
        else if (gdriveParam === 'error') UI.toast('Could not connect Google Drive. Please try again.', 'error');
        if (gdriveParam) {
            this.hashQuery = '';
            window.location.hash = '#/my-autopilot'; // consume the one-shot param so it doesn't re-toast
        }

        try {
            const [autopilotRes, driveRes, mediaRes] = await Promise.all([
                API.getAutopilotSettings(),
                API.getDriveStatus(),
                API.getMediaQueue(),
            ]);
            const a = autopilotRes.autopilot;
            const drive = driveRes;
            const assets = mediaRes.assets || [];
            const body = document.getElementById('page-body');

            let folderOptions = '';
            if (drive.connected) {
                try {
                    const { folders } = await API.listDriveFolders();
                    folderOptions = folders.map(f =>
                        `<option value="${UI.esc(f.id)}" ${f.id === drive.folder_id ? 'selected' : ''}>${UI.esc(f.name)}</option>`
                    ).join('');
                } catch (err) {
                    folderOptions = '';
                }
            }

            body.innerHTML = `
                <div style="max-width: 700px;">
                    <div class="card mb-3 slide-up">
                        <div class="card-header">
                            <h3>Autopilot Settings</h3>
                            <button class="btn btn-sm btn-secondary" onclick="App.runAutopilotNow()">Run Now</button>
                        </div>
                        <div class="card-body">
                            <p class="text-sm text-muted mb-2">When enabled, the growth agent picks the oldest queued media, writes a caption, and ${a.auto_publish ? 'publishes' : 'drafts for your approval'} it on a recurring schedule.</p>
                            <form id="autopilot-form" onsubmit="App.saveAutopilotSettings(event)">
                                <div class="form-group">
                                    <label><input type="checkbox" id="ap-enabled" ${a.enabled ? 'checked' : ''}> Enable autopilot</label>
                                </div>
                                <div class="form-group">
                                    <label><input type="checkbox" id="ap-auto-publish" ${a.auto_publish ? 'checked' : ''}> Auto-publish (off = draft for approval)</label>
                                </div>
                                <div class="form-group">
                                    <label>Posts per week</label>
                                    <input class="form-input" type="number" id="ap-posts-per-week" min="1" value="${a.posts_per_week ?? 3}">
                                </div>
                                <div class="form-group">
                                    <label>Preferred hours (24h, comma-separated)</label>
                                    <input class="form-input" id="ap-preferred-hours" placeholder="9, 13, 19" value="${UI.esc((a.preferred_hours || []).join(', '))}">
                                </div>
                                <div class="form-group">
                                    <label>Timezone (IANA, e.g. America/New_York)</label>
                                    <input class="form-input" id="ap-timezone" value="${UI.esc(a.timezone || 'UTC')}">
                                </div>
                                <div class="form-group">
                                    <label>Niche</label>
                                    <input class="form-input" id="ap-niche" value="${UI.esc(a.niche || '')}">
                                </div>
                                <div class="form-group">
                                    <label>Tone</label>
                                    <input class="form-input" id="ap-tone" value="${UI.esc(a.tone || '')}">
                                </div>
                                <div class="form-group">
                                    <label>Goal</label>
                                    <input class="form-input" id="ap-goal" value="${UI.esc(a.goal || '')}">
                                </div>
                                <div class="form-group">
                                    <label>Target location</label>
                                    <input class="form-input" id="ap-location" value="${UI.esc(a.target_location || '')}">
                                </div>
                                <button class="btn btn-primary mt-1" type="submit" id="ap-save-btn">Save Settings</button>
                            </form>
                        </div>
                    </div>

                    <div class="card mb-3 slide-up">
                        <div class="card-header">
                            <h3>Google Drive</h3>
                        </div>
                        <div class="card-body">
                            ${drive.connected ? `
                                <p class="text-sm mb-2">Connected as <strong>${UI.esc(drive.email || 'unknown')}</strong>.</p>
                                <div class="form-group">
                                    <label>Folder to sync media from</label>
                                    <select class="form-input" id="drive-folder-select">
                                        <option value="">— Select a folder —</option>
                                        ${folderOptions}
                                    </select>
                                </div>
                                <div class="d-flex gap-2">
                                    <button class="btn btn-secondary btn-sm" onclick="App.saveDriveFolder()">Save Folder</button>
                                    <button class="btn btn-secondary btn-sm" onclick="App.syncDriveNow()">Sync Now</button>
                                    <button class="btn btn-danger btn-sm" onclick="App.disconnectDrive()">Disconnect</button>
                                </div>
                                ${drive.folder_name ? `<p class="text-xs text-muted mt-1">Currently syncing from: ${UI.esc(drive.folder_name)}</p>` : '<p class="text-xs text-muted mt-1">Pick a folder above, then Save.</p>'}
                            ` : `
                                <p class="text-sm text-muted mb-2">Link your Google Drive so autopilot can pull new photos/videos straight from a folder you choose.</p>
                                <button class="btn btn-primary btn-sm" onclick="App.connectGoogleDrive()">Connect Google Drive</button>
                            `}
                        </div>
                    </div>

                    <div class="card slide-up">
                        <div class="card-header">
                            <h3>Media Queue</h3>
                        </div>
                        <div class="card-body">
                            <form id="upload-form" class="mb-2" onsubmit="App.uploadMediaFile(event)">
                                <div class="form-group">
                                    <label>Upload media</label>
                                    <input class="form-input" type="file" id="upload-file" accept="image/*,video/*" required>
                                </div>
                                <div class="form-group">
                                    <label>Caption hint (optional)</label>
                                    <input class="form-input" id="upload-caption-hint" placeholder="What's in this photo/video?">
                                </div>
                                <button class="btn btn-secondary btn-sm" type="submit">Add to Queue</button>
                            </form>
                            ${assets.length === 0 ? UI.emptyState('🖼️', 'Queue is empty', 'Upload media or connect Google Drive to get started.') : `
                                <table class="table">
                                    <thead><tr><th>Type</th><th>Source</th><th>Status</th><th>Added</th><th></th></tr></thead>
                                    <tbody>
                                        ${assets.map(asset => `
                                            <tr>
                                                <td>${UI.esc(asset.media_type)}</td>
                                                <td>${asset.source === 'google_drive' ? 'Google Drive' : 'Uploaded'}</td>
                                                <td>${UI.badge(asset.status)}</td>
                                                <td>${UI.timeAgo(asset.created_at)}</td>
                                                <td>${asset.status === 'queued' ? `<button class="btn btn-sm btn-danger" onclick="App.deleteMediaAsset('${asset.id}')">Delete</button>` : ''}</td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            `}
                        </div>
                    </div>
                </div>
            `;
        } catch (err) {
            document.getElementById('page-body').innerHTML = `<p style="color:var(--accent-red);">Error: ${UI.esc(err.message)}</p>`;
        }
    },

    async saveAutopilotSettings(e) {
        e.preventDefault();
        const btn = document.getElementById('ap-save-btn');
        btn.disabled = true;
        btn.textContent = 'Saving...';

        try {
            const hoursRaw = document.getElementById('ap-preferred-hours').value;
            const preferred_hours = hoursRaw
                .split(',')
                .map(s => parseInt(s.trim(), 10))
                .filter(n => !isNaN(n));

            await API.updateAutopilotSettings({
                enabled: document.getElementById('ap-enabled').checked,
                auto_publish: document.getElementById('ap-auto-publish').checked,
                posts_per_week: parseInt(document.getElementById('ap-posts-per-week').value, 10) || 1,
                preferred_hours,
                timezone: document.getElementById('ap-timezone').value || 'UTC',
                niche: document.getElementById('ap-niche').value,
                tone: document.getElementById('ap-tone').value,
                goal: document.getElementById('ap-goal').value,
                target_location: document.getElementById('ap-location').value,
            });
            UI.toast('Autopilot settings saved!', 'success');
            this.route();
        } catch (err) {
            UI.toast(err.message, 'error');
            btn.disabled = false;
            btn.textContent = 'Save Settings';
        }
    },

    async runAutopilotNow() {
        UI.toast('Asking the growth agent to plan a post...', 'info');
        try {
            const result = await API.runAutopilotNow();
            if (result.status === 'planned') {
                UI.toast('Post planned! Check My Posts.', 'success');
            } else {
                UI.toast(result.message || 'No post was planned.', 'info');
            }
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    async connectGoogleDrive() {
        try {
            const { url } = await API.getDriveAuthUrl();
            window.location.href = url;
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    async saveDriveFolder() {
        const select = document.getElementById('drive-folder-select');
        const folderId = select.value;
        if (!folderId) {
            UI.toast('Pick a folder first.', 'error');
            return;
        }
        const folderName = select.options[select.selectedIndex].textContent;
        try {
            await API.setDriveFolder(folderId, folderName);
            UI.toast('Folder saved.', 'success');
            this.route();
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    async syncDriveNow() {
        UI.toast('Syncing from Google Drive...', 'info');
        try {
            const { imported } = await API.syncDriveNow();
            UI.toast(imported > 0 ? `Imported ${imported} new file(s).` : 'No new files found.', 'success');
            this.route();
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    async disconnectDrive() {
        const ok = await UI.confirm('Disconnect Google Drive', 'Autopilot will stop syncing media from Drive. Already-queued items are kept.');
        if (!ok) return;
        try {
            await API.disconnectDrive();
            UI.toast('Google Drive disconnected.', 'success');
            this.route();
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    async uploadMediaFile(e) {
        e.preventDefault();
        const fileInput = document.getElementById('upload-file');
        const file = fileInput.files[0];
        if (!file) return;
        const captionHint = document.getElementById('upload-caption-hint').value;

        try {
            await API.uploadMedia(file, captionHint);
            UI.toast('Added to media queue.', 'success');
            this.route();
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    async deleteMediaAsset(assetId) {
        const ok = await UI.confirm('Delete Media', 'Remove this item from the queue?');
        if (!ok) return;
        try {
            await API.deleteMediaAsset(assetId);
            UI.toast('Deleted.', 'success');
            this.route();
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },

    // ══════════════════════════════════════════════════════════════════
    // CUSTOMER: Settings
    // ══════════════════════════════════════════════════════════════════

    async renderMySettings(root) {
        this.withLayout(root, 'my-settings', 'Settings', '', UI.loading());

        try {
            const profile = await API.getMyProfile();
            const c = profile.customer;
            const body = document.getElementById('page-body');

            body.innerHTML = `
                <div style="max-width: 600px;">
                    <div class="card mb-3 slide-up">
                        <div class="card-header">
                            <h3>Instagram Credentials</h3>
                            <button class="btn btn-sm btn-secondary" onclick="App.testMyToken()">Test Connection</button>
                        </div>
                        <div class="card-body">
                            <p class="text-sm text-muted mb-2">Connect your Instagram Business/Creator account by providing your Meta access token and User ID.</p>
                            <form id="settings-form" onsubmit="App.saveSettings(event)">
                                <div class="form-group">
                                    <label>Instagram User ID</label>
                                    <input class="form-input" id="set-ig-user-id" value="${UI.esc(c.ig_user_id || '')}" placeholder="e.g. 17841405..." required>
                                </div>
                                <div class="form-group">
                                    <label>Access Token</label>
                                    <input class="form-input" id="set-token" placeholder="Paste your long-lived token here" required>
                                    <small class="text-muted text-xs">Get this from the <a href="https://developers.facebook.com/tools/explorer/" target="_blank">Meta Graph Explorer</a></small>
                                </div>
                                <div class="form-group">
                                    <label>Login Kind</label>
                                    <select class="form-input" id="set-login-kind">
                                        <option value="ig_login" ${c.login_kind === 'ig_login' ? 'selected' : ''}>Instagram Login</option>
                                        <option value="fb_login" ${c.login_kind === 'fb_login' ? 'selected' : ''}>Facebook Login</option>
                                    </select>
                                </div>
                                <button class="btn btn-primary mt-1" type="submit" id="save-btn">Save Credentials</button>
                            </form>
                        </div>
                    </div>

                    <div class="card slide-up">
                        <div class="card-header">
                            <h3>Account Info</h3>
                        </div>
                        <div class="card-body">
                            <div class="detail-grid">
                                <div class="detail-field">
                                    <span class="detail-label">Name</span>
                                    <span class="detail-value">${UI.esc(c.name)}</span>
                                </div>
                                <div class="detail-field">
                                    <span class="detail-label">Email</span>
                                    <span class="detail-value">${UI.esc(c.email)}</span>
                                </div>
                                <div class="detail-field">
                                    <span class="detail-label">Status</span>
                                    <span class="detail-value">${UI.badge(c.status)}</span>
                                </div>
                                <div class="detail-field">
                                    <span class="detail-label">IG Username</span>
                                    <span class="detail-value">${c.ig_username ? '@' + UI.esc(c.ig_username) : '—'}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        } catch (err) {
            document.getElementById('page-body').innerHTML = `<p style="color:var(--accent-red);">Error: ${UI.esc(err.message)}</p>`;
        }
    },

    async saveSettings(e) {
        e.preventDefault();
        const btn = document.getElementById('save-btn');
        btn.disabled = true;
        btn.textContent = 'Saving...';

        try {
            await API.updateMyProfile({
                ig_user_id: document.getElementById('set-ig-user-id').value,
                ig_access_token: document.getElementById('set-token').value,
                login_kind: document.getElementById('set-login-kind').value,
            });
            UI.toast('Credentials saved!', 'success');
            btn.textContent = 'Saved ✓';
            setTimeout(() => { btn.disabled = false; btn.textContent = 'Save Credentials'; }, 2000);
        } catch (err) {
            UI.toast(err.message, 'error');
            btn.disabled = false;
            btn.textContent = 'Save Credentials';
        }
    },

    async testMyToken() {
        UI.toast('Testing connection...', 'info');
        try {
            const result = await API.testMyToken();
            if (result.status === 'ok') {
                UI.toast(`Connected! @${result.profile.username} (${result.profile.followers_count} followers)`, 'success');
            } else {
                UI.toast(`Error: ${result.message}`, 'error');
            }
        } catch (err) {
            UI.toast(err.message, 'error');
        }
    },
};

// ── Bootstrap ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    // Handle login/signup pages specially (no sidebar)
    const hash = window.location.hash || '#/login';
    if (hash === '#/login' || hash === '#/signup' || !API.isLoggedIn()) {
        App.renderLoginPage(document.getElementById('app'));
        // Listen for hash changes on login pages
        window.addEventListener('hashchange', () => {
            const h = window.location.hash;
            if (h === '#/login' || h === '#/signup') {
                App.renderLoginPage(document.getElementById('app'));
            } else {
                App.route();
            }
        });
    } else {
        App.init();
    }
});
