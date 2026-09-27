"use client"

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { api, ApiRequestError, unwrap, type Schemas } from '@/lib/api/client';

/** `GET /api/auth/me` user. */
export type User = Schemas["CurrentUser"];

/** The fields the annotation / highlight UI shows for a note's author. */
export type BasicUser = Pick<User, "name" | "picture">;

interface AuthContextType {
	user: User | null;
	loading: boolean;
	error: string | null;
	logout: (allDevices?: boolean) => Promise<void>;
}

const AUTH_STORAGE_KEY = 'auth_user';

/** Waits between `/api/auth/me` attempts (~13s total), so a server restart
 * (network error, or 502/503 from Caddy) doesn't bounce the user to /login. */
const AUTH_RETRY_DELAYS_MS = [1000, 2000, 4000, 6000];

/** Network errors and 5xx/408/429 are worth retrying; a 4xx is a real answer. */
function isTransient(err: unknown): boolean {
	if (!(err instanceof ApiRequestError)) return true;
	return err.status >= 500 || err.status === 408 || err.status === 429;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
	// Always initialize to null to match server render and avoid hydration mismatch.
	// localStorage is read in the effect below for fast optimistic state.
	const [user, setUser] = useState<User | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	// Sync user state with localStorage whenever it changes
	useEffect(() => {
		if (user) {
			localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(user));
		} else {
			localStorage.removeItem(AUTH_STORAGE_KEY);
		}
	}, [user]);

	// Check if user is logged in
	useEffect(() => {
		// Immediately restore cached user for fast UI update
		const storedUser = localStorage.getItem(AUTH_STORAGE_KEY);
		if (storedUser) {
			try {
				setUser(JSON.parse(storedUser));
			} catch {
				localStorage.removeItem(AUTH_STORAGE_KEY);
			}
		}

		let cancelled = false;

		async function checkAuth() {
			for (let attempt = 0; ; attempt++) {
				try {
					const response = await unwrap(api.GET('/api/auth/me'));
					if (cancelled) return;
					// `success: false` is the server's "not authenticated" answer.
					setUser(response.success && response.user ? response.user : null);
					setError(null);
				} catch (err) {
					if (cancelled) return;
					if (isTransient(err) && attempt < AUTH_RETRY_DELAYS_MS.length) {
						console.warn(`Auth check failed (attempt ${attempt + 1}), retrying:`, err);
						await new Promise((resolve) => setTimeout(resolve, AUTH_RETRY_DELAYS_MS[attempt]));
						if (cancelled) return;
						continue;
					}
					console.error('Auth check failed:', err);
					if (isTransient(err)) setError('Failed to check authentication status');
					setUser(null);
				}
				setLoading(false);
				return;
			}
		}

		checkAuth();
		return () => {
			cancelled = true;
		};
	}, []);

	// Logout user
	const logout = async (allDevices = false) => {
		try {
			setLoading(true);
			await unwrap(api.GET('/api/auth/logout', { params: { query: { all_devices: allDevices } } }));
			setUser(null);
		} catch (err) {
			console.error('Logout failed:', err);
			setError('Failed to logout');
		} finally {
			setLoading(false);
		}
	};

	return (
		<AuthContext.Provider value={{ user, loading, error, logout }}>
			{children}
		</AuthContext.Provider>
	);
}

export function useAuth() {
	const context = useContext(AuthContext);
	if (context === undefined) {
		throw new Error('useAuth must be used within an AuthProvider');
	}
	return context;
}
