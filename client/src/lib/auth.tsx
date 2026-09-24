"use client"

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { api, unwrap } from '@/lib/api/client';

/**
 * Compat — the server's `Schemas["CurrentUser"]` has `name` / `picture`
 * nullable; these keep the old non-null shape until the pages and top-level
 * components that read them handle null.
 */
export interface BasicUser {
	name: string;
	picture: string;
	id?: string;
}

export interface User extends BasicUser {
	id: string;
	email: string;
}

interface AuthContextType {
	user: User | null;
	loading: boolean;
	error: string | null;
	logout: (allDevices?: boolean) => Promise<void>;
}

const AUTH_STORAGE_KEY = 'auth_user';

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

		async function checkAuth() {
			try {
				const response = await unwrap(api.GET('/api/auth/me'));
				if (response.success && response.user) {
					setUser(response.user as User);
				} else {
					// Auth check failed, clear the user
					setUser(null);
				}
			} catch (err) {
				console.error('Auth check failed:', err);
				setError('Failed to check authentication status');
				// Also clear the user on error
				setUser(null);
			} finally {
				setLoading(false);
			}
		}

		checkAuth();
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
