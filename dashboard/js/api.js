/**
 * OpenInstaFlow Dashboard — API Client
 * 
 * Handles all REST communication with the backend.
 * Manages JWT tokens in localStorage.
 */

const API = {
    BASE: '/api',

    // ── Token management ──────────────────────────────────────────────
    getToken() {
        return localStorage.getItem('oif_token');
    },

    getRole() {
        return localStorage.getItem('oif_role');
    },

    getUserData() {
        const raw = localStorage.getItem('oif_user');
        return raw ? JSON.parse(raw) : null;
    },

    saveAuth(data) {
        localStorage.setItem('oif_token', data.access_token);
        localStorage.setItem('oif_role', data.role);
        if (data.customer) {
            localStorage.setItem('oif_user', JSON.stringify(data.customer));
        } else if (data.email) {
            localStorage.setItem('oif_user', JSON.stringify({ email: data.email }));
        }
    },

    clearAuth() {
        localStorage.removeItem('oif_token');
        localStorage.removeItem('oif_role');
        localStorage.removeItem('oif_user');
    },

    isLoggedIn() {
        return !!this.getToken();
    },

    isAdmin() {
        return this.getRole() === 'admin';
    },

    // ── HTTP helpers ──────────────────────────────────────────────────
    async _fetch(endpoint, options = {}) {
        const url = `${this.BASE}${endpoint}`;
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers,
        };

        const token = this.getToken();
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        try {
            const resp = await fetch(url, {
                ...options,
                headers,
            });

            if (resp.status === 401) {
                this.clearAuth();
                window.location.hash = '#/login';
                throw new Error('Session expired. Please log in again.');
            }

            const data = await resp.json();

            if (!resp.ok) {
                throw new Error(data.detail || data.message || `HTTP ${resp.status}`);
            }

            return data;
        } catch (err) {
            if (err.message.includes('Session expired')) throw err;
            throw err;
        }
    },

    get(endpoint) {
        return this._fetch(endpoint, { method: 'GET' });
    },

    post(endpoint, body) {
        return this._fetch(endpoint, {
            method: 'POST',
            body: JSON.stringify(body),
        });
    },

    put(endpoint, body) {
        return this._fetch(endpoint, {
            method: 'PUT',
            body: JSON.stringify(body),
        });
    },

    delete(endpoint) {
        return this._fetch(endpoint, { method: 'DELETE' });
    },

    // ── Auth endpoints ────────────────────────────────────────────────
    adminLogin(email, password) {
        return this.post('/auth/admin/login', { email, password });
    },

    customerLogin(email, password) {
        return this.post('/auth/customer/login', { email, password });
    },

    customerSignup(email, password, name, activation_code) {
        return this.post('/auth/customer/signup', { email, password, name, activation_code });
    },

    // ── Admin endpoints ───────────────────────────────────────────────
    getDashboardStats() {
        return this.get('/dashboard/stats');
    },

    getCustomers() {
        return this.get('/customers');
    },

    getCustomer(id) {
        return this.get(`/customers/${id}`);
    },

    updateCustomer(id, data) {
        return this.put(`/customers/${id}`, data);
    },

    deleteCustomer(id) {
        return this.delete(`/customers/${id}`);
    },

    testCustomerToken(id) {
        return this.post(`/customers/${id}/test`, {});
    },

    getCustomerPosts(id) {
        return this.get(`/customers/${id}/posts`);
    },

    publishForCustomer(id, data) {
        return this.post(`/customers/${id}/publish`, data);
    },

    scheduleForCustomer(id, data) {
        return this.post(`/customers/${id}/schedule`, data);
    },

    getActivationCodes() {
        return this.get('/activation-codes');
    },

    generateActivationCodes(count) {
        return this.post('/activation-codes', { count });
    },

    deleteActivationCode(id) {
        return this.delete(`/activation-codes/${id}`);
    },

    getAllPosts(status) {
        const qs = status ? `?status=${status}` : '';
        return this.get(`/posts${qs}`);
    },

    cancelPost(id) {
        return this.delete(`/posts/${id}`);
    },

    getActivity(limit = 50) {
        return this.get(`/activity?limit=${limit}`);
    },

    // ── Admin: media queue (cross-customer) ────────────────────────────
    getAllMedia(status) {
        const qs = status ? `?status=${status}` : '';
        return this.get(`/media${qs}`);
    },

    getCustomerMedia(customerId) {
        return this.get(`/customers/${customerId}/media`);
    },

    deleteMediaAdmin(id) {
        return this.delete(`/media/${id}`);
    },

    // ── Customer self-serve endpoints ─────────────────────────────────
    getMyProfile() {
        return this.get('/me');
    },

    updateMyProfile(data) {
        return this.put('/me', data);
    },

    testMyToken() {
        return this.post('/me/test-token', {});
    },

    getMyPosts(limit = 50) {
        return this.get(`/me/posts?limit=${limit}`);
    },

    publishMyPost(data) {
        return this.post('/me/publish', data);
    },

    scheduleMyPost(data) {
        return this.post('/me/schedule', data);
    },

    cancelMyPost(id) {
        return this.delete(`/me/posts/${id}`);
    },

    getMyActivity(limit = 30) {
        return this.get(`/me/activity?limit=${limit}`);
    },

    // ── Customer: autopilot ────────────────────────────────────────────
    getAutopilotSettings() {
        return this.get('/me/autopilot');
    },

    updateAutopilotSettings(data) {
        return this.put('/me/autopilot', data);
    },

    runAutopilotNow() {
        return this.post('/me/autopilot/run-now', {});
    },

    // ── Customer: media queue ──────────────────────────────────────────
    getMediaQueue(status) {
        const qs = status ? `?status=${status}` : '';
        return this.get(`/me/media${qs}`);
    },

    async uploadMedia(file, captionHint) {
        // Direct-to-R2 upload: presign -> PUT straight to R2 (file bytes never touch our
        // API) -> confirm, which is what actually creates the MediaAsset row.
        const { object_key, upload_url } = await this.post('/me/media/presign', { filename: file.name });

        const putResp = await fetch(upload_url, {
            method: 'PUT',
            headers: { 'Content-Type': file.type || 'application/octet-stream' },
            body: file,
        });
        if (!putResp.ok) {
            throw new Error(`Upload to storage failed (HTTP ${putResp.status}). Please try again.`);
        }

        return this.post('/me/media/confirm', { object_key, caption_hint: captionHint || undefined });
    },

    deleteMediaAsset(id) {
        return this.delete(`/me/media/${id}`);
    },

    // ── Customer: Google Drive ─────────────────────────────────────────
    getDriveStatus() {
        return this.get('/me/google-drive/status');
    },

    getDriveAuthUrl() {
        return this.get('/me/google-drive/auth-url');
    },

    listDriveFolders() {
        return this.get('/me/google-drive/folders');
    },

    setDriveFolder(folder_id, folder_name) {
        return this.put('/me/google-drive/folder', { folder_id, folder_name });
    },

    syncDriveNow() {
        return this.post('/me/google-drive/sync-now', {});
    },

    disconnectDrive() {
        return this.post('/me/google-drive/disconnect', {});
    },
};
