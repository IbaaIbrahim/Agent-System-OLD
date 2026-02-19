
/**
 * Auth Client to communicate with the Auth Broker service.
 */

const DEFAULT_BROKER_URL = import.meta.env.VITE_AUTH_BROKER_URL || "http://localhost:11700";

export interface TokenResponse {
    access_token: string;
    token_type: string;
}

export class AuthClient {
    private static brokerUrl: string = DEFAULT_BROKER_URL;

    /**
     * Configure the broker URL. Call before getInitialToken/refreshToken.
     */
    static configure(brokerUrl: string) {
        this.brokerUrl = brokerUrl;
    }

    /**
     * Fetches a new access token from the auth broker.
     */
    static async getInitialToken(): Promise<string> {
        try {
            const response = await fetch(`${this.brokerUrl}/request-token`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({})
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Failed to fetch token: ${response.status} ${errorText}`);
            }

            const data: TokenResponse = await response.json();
            return data.access_token;
        } catch (error) {
            console.error('Error fetching initial token:', error);
            throw error;
        }
    }

    /**
     * Refreshes the token (in this demo, it just requests a new one from broker).
     */
    static async refreshToken(): Promise<string> {
        return this.getInitialToken();
    }
}
