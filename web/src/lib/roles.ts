/** Whether a person may use the administrator pages: they hold the admin role and have not been banned. */
export const isActiveAdmin = (user: { role?: string | null; banned?: boolean | null }): boolean => user.role === "admin" && !user.banned;
