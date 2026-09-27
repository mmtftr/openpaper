"use client"

import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AlertCircle, ArrowLeft, Loader2 } from "lucide-react";
import Image from "next/image";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { api, unwrap } from "@/lib/api/client";
import { Badge } from "@/components/ui/badge";
import { safeReturnTo } from "@/lib/utils";

function LoginContent() {
	const { user, loading, error: authError } = useAuth();
	const [error, setError] = useState<string | null>(null);
	const router = useRouter();
	const searchParams = useSearchParams();
	const returnToParam = safeReturnTo(searchParams.get('returnTo'));
	const returnTo = returnToParam || '/';
	const errorParam = searchParams.get('error');

	const [email, setEmail] = useState('');
	const [showOtp, setShowOtp] = useState(false);
	const [emailError, setEmailError] = useState<string | null>(null);
	const [isEmailLoading, setIsEmailLoading] = useState(false);
	const [showNameInput, setShowNameInput] = useState(false);
	const [firstName, setFirstName] = useState('');
	const [lastName, setLastName] = useState('');
	const [lastUsedProvider, setLastUsedProvider] = useState<string | null>(null);

	useEffect(() => {
		const storedProvider = localStorage.getItem('signin-provider');
		if (storedProvider) {
			setLastUsedProvider(storedProvider);
		}
	}, []);


	// Handle error query param
	useEffect(() => {
		if (errorParam) {
			switch (errorParam) {
				case 'callback_failed':
					setError('Login failed. Please try again.');
					break;
				case 'authentication_error':
					setError('Authentication error occurred. Please try again.');
					break;
				case 'missing_code':
					setError('Authentication code missing. Please try again.');
					break;
				case 'different_provider':
					setError('This email is already associated with a different sign-in method. Please use your original sign-in method.');
					break;
				default:
					setError('An error occurred during login. Please try again.');
			}
		}
	}, [errorParam]);

	// If user is already logged in, redirect to return path
	useEffect(() => {
		if (user && !loading) {
			router.push(returnTo);
		}
	}, [user, loading, router, returnTo]);

	const handleBackToStart = () => {
		setShowNameInput(false);
		setShowOtp(false);
		setEmailError(null);
	};

	const handleEmailSignIn = async (e: React.FormEvent) => {
		e.preventDefault();
		localStorage.setItem('signin-provider', 'email');
		setIsEmailLoading(true);
		setEmailError(null);
		try {
			const data = await unwrap(api.POST('/api/auth/email/signin', {
				body: { email },
			}));
			if (data.success) {
				setError(null);
				if (data.newly_created || data.needs_name) {
					setShowNameInput(true);
				} else {
					setShowOtp(true);
				}
			} else {
				setEmailError(data.message || 'Failed to send verification code.');
			}
		} catch (error) {
			if (error instanceof Error) {
				setEmailError(error.message);
			} else {
				setEmailError('An unexpected error occurred.');
			}
		} finally {
			setIsEmailLoading(false);
		}
	};

	const handleNameSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!firstName || !lastName) {
			setEmailError("Please enter your full name.");
			return;
		}
		setIsEmailLoading(true);
		setEmailError(null);
		try {
			const name = `${firstName} ${lastName}`;
			const data = await unwrap(api.POST('/api/auth/email/fullname', {
				body: { email, name },
			}));
			if (data.success) {
				setShowNameInput(false);
				setShowOtp(true);
			} else {
				setEmailError(data.message || 'Failed to set name.');
			}
		} catch (error) {
			if (error instanceof Error) {
				setEmailError(error.message);
			} else {
				setEmailError('An unexpected error occurred.');
			}
		} finally {
			setIsEmailLoading(false);
		}
	};

	const handleVerifyCode = async (code: string) => {
		setIsEmailLoading(true);
		setEmailError(null);
		try {
			const data = await unwrap(api.POST('/api/auth/email/verify', {
				body: { email, code },
			}));

			if (!data.success) {
				setEmailError(data.message || 'Failed to verify code.');
				setIsEmailLoading(false);
				return;
			}

			if (data.redirectUrl) {
				// The server sends us to /auth/callback, which lands on the
				// stored returnTo; a ?returnTo= from the middleware wins over
				// an older stored one.
				if (returnToParam) localStorage.setItem('returnTo', returnToParam);
				window.location.href = data.redirectUrl;
				return;
			}

			router.push(returnTo);
		} catch (error) {
			if (error instanceof Error) {
				setEmailError(error.message);
			} else {
				setEmailError('An unexpected error occurred.');
			}
		} finally {
			setIsEmailLoading(false);
		}
	};


	if (loading) {
		return (
			<div className="flex flex-1 items-center justify-center py-8">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}

	let headerContent = {
		title: "Sign in to Open Paper",
		description: "Connect with an account to access your papers, projects, and annotations."
	};

	if (showNameInput) {
		headerContent = {
			title: "What's your name?",
			description: "This will be displayed on your profile."
		};
	} else if (showOtp) {
		headerContent = {
			title: "Check your email",
			description: `Enter the 6-digit code we sent to ${email}. This will expire in 10 minutes.`,
		};
	}

	return (
		<div className="flex flex-1 items-center justify-center px-4 py-10">
			<Card className="relative w-full max-w-sm animate-rise-in gap-5 py-7 shadow-md sm:max-w-md">
				<CardHeader className="gap-2 px-6 text-center sm:px-7">
					{(showNameInput || showOtp) && (
						<Button variant="ghost" size="icon" aria-label="Back" className="absolute top-4 left-4" onClick={handleBackToStart}>
							<ArrowLeft className="h-5 w-5" />
						</Button>
					)}
					<Image src="/openpaper.svg" width={40} height={40} alt="" className="mx-auto mb-2" />
					<CardTitle className="text-xl font-semibold tracking-tight sm:text-2xl">{headerContent.title}</CardTitle>
					<CardDescription className="text-balance">{headerContent.description}</CardDescription>
				</CardHeader>
				<CardContent className="px-6 sm:px-7">
					<div className="space-y-4">
						{(error || authError) && (
							<Alert variant="destructive">
								<AlertCircle className="h-4 w-4" />
								<AlertDescription>
									{error || authError}
								</AlertDescription>
							</Alert>
						)}

						{showOtp ? (
							<div className="space-y-4 text-center">
								<div className="flex justify-center">
									<InputOTP maxLength={6} onComplete={handleVerifyCode} disabled={isEmailLoading}>
										<InputOTPGroup>
											<InputOTPSlot index={0} />
											<InputOTPSlot index={1} />
											<InputOTPSlot index={2} />
											<InputOTPSlot index={3} />
											<InputOTPSlot index={4} />
											<InputOTPSlot index={5} />
										</InputOTPGroup>
									</InputOTP>
								</div>
								{isEmailLoading && <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" />}
							</div>
						) : showNameInput ? (
							<form onSubmit={handleNameSubmit}>
								<div className="space-y-3">
									<Input
										aria-label="First name"
										autoComplete="given-name"
										autoFocus
										className="h-10"
										placeholder="First Name"
										value={firstName}
										onChange={(e) => setFirstName(e.target.value)}
										disabled={isEmailLoading}
										required
									/>
									<Input
										aria-label="Last name"
										autoComplete="family-name"
										className="h-10"
										placeholder="Last Name"
										value={lastName}
										onChange={(e) => setLastName(e.target.value)}
										disabled={isEmailLoading}
										required
									/>
									<Button
										type="submit"
										className="h-10 w-full"
										disabled={isEmailLoading || !firstName || !lastName}
									>
										{isEmailLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Continue"}
									</Button>
								</div>
							</form>
						) : (
							<form onSubmit={handleEmailSignIn}>
								<div className="space-y-3">
									<Input
										type="email"
										aria-label="Email"
										autoComplete="email"
										inputMode="email"
										autoFocus
										className="h-10"
										placeholder="m@example.com"
										value={email}
										onChange={(e) => setEmail(e.target.value)}
										disabled={isEmailLoading}
										required
									/>
									<Button
										type="submit"
										className="relative h-10 w-full"
										disabled={isEmailLoading || !email}
									>
										{isEmailLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Continue with Email"}
										{!isEmailLoading && lastUsedProvider === 'email' && <Badge variant="secondary" className="absolute right-2">Last used</Badge>}
									</Button>
								</div>
							</form>
						)}

						{emailError && (
							<Alert variant="destructive">
								<AlertCircle className="h-4 w-4" />
								<AlertDescription>
									{emailError}
								</AlertDescription>
							</Alert>
						)}
					</div>
				</CardContent>
			</Card>
		</div>
	);
}

export default function LoginPage() {
	return (
		<Suspense fallback={
			<div className="flex flex-1 items-center justify-center">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		}>
			<LoginContent />
		</Suspense>
	)
}
