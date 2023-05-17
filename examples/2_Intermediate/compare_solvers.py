import numpy as np
from matplotlib import pyplot as plt
from scipy.io import loadmat 
from scipy.linalg import cholesky
#from sksparse.cholmod import cholesky
from scipy.sparse import csc_matrix, spdiags, vstack, hstack, eye
from scipy.sparse.linalg import minres, LinearOperator
import sys
import time
from simsoptpp import matmult

data = loadmat("optimization_matrices.mat")
A1 = data['A']
b1 = data['b']
C = data['C']
n = A1.shape[1]
m = A1.shape[0]
k = C.shape[0]
C[np.abs(C) < 1e-10] = 0.0
C = csc_matrix(C)
A_I = data['A_I']
b_I = data['b_I'][0]


# overall constraint matrix
scale = 1e6;    # scaling factor
A1 *= scale
b1 *= scale
A_I *= scale
b_I *= scale
A = np.vstack((A1, A_I))
b = np.vstack((b1, b_I))
C *= scale

# let's study the singular values of A and of C
# sv_A = np.linalg.svd(A)
# plt.figure(1)
# plt.semilogy(sv_A)
# plt.show()
#some tests with (A'*A + 1/nu)*x = A'*b
kappa = 1e-100
tol = 1e-16
maxiter = 2000 
sigma = 1
nu = 1e15 / (scale ** 2)     # Scale so that 1/nu is somewhat comparable to norm(A.'*A);
CT = C.T
AT = A.T
rhs = np.vstack((AT @ b + np.ones((n, 1)) / nu, np.zeros((k, 1))))
kappa_nu = kappa + 1 / nu


def A_fun(x):
    alpha = x[:n]
    Ax = np.squeeze(np.vstack(((AT @ (A @ alpha) + kappa_nu * alpha + CT @ x[n:])[:, np.newaxis], (C @ alpha)[:, np.newaxis])), axis=-1)
    return Ax


def callback(xk):
    xk = xk[:, np.newaxis]
    fB = np.linalg.norm(A1 @ xk[:n, :] - b1) ** 2 / scale ** 2
    fI = sigma * np.linalg.norm(A_I @ xk[:n, :] - b_I) ** 2 / scale ** 2
    fK = kappa * np.linalg.norm(xk[:n, :]) ** 2  # / scale ** 2,
    f = fB + fI + fK
    print(f, fB, fI, fK,
          np.linalg.norm(C @ xk[:n, :]))


A_operator = LinearOperator((n + k, n + k), matvec=A_fun)
# solve unpreconditioned saddle point problem with MINRES
sys.stdout = open('output0.txt', 'w')
print('Total ', 'fB ', 'sigma * fI ', 'kappa * fK ', 'C*alpha')
t1 = time.time()
sol0, info0 = minres(A_operator, rhs, x0=np.zeros((n + k, 1)), tol=tol, maxiter=maxiter, callback=callback)
t2 = time.time()
sys.stdout.close()
sys.stdout = open("/dev/stdout", "w")
t3 = t2 - t1
print('First minres took = ', t3, ' s')
f0, fB0, fI0, fK0, fC0 = np.loadtxt('output0.txt', skiprows=1, unpack=True)
#print(f0)

# %% lets try to construct a preconditioner
AAdiag = np.sum(A ** 2, axis=0) + kappa_nu   # diagonal of A'*A + 1/nu*I
AAdiagT = AAdiag.T
AAdiag_inv = spdiags(1 / AAdiagT, 0, n, n)

# %% Schur complement is -C*Ainv*C' -- let's use the inverse diagonal of A:
S = C.dot(AAdiag_inv.dot(CT))   # should be a sparse matrix
# % compute incomp. Choleksy of slightly regularized matrix to avoid zero
# % eigenvalues:
S_chol = cholesky((S + spdiags(np.ones(k), 0, k, k)).todense())
#S_chol = cholesky((S + spdiags(1e-3 * np.ones(k), 0, k, k)).todense(), lower=True)
S_chol[np.abs(S_chol) < 1e-10] = 0.0
S_chol = csc_matrix(S_chol)

print('S_chol = ', S_chol)
# exit()

# # % precondition factors:
P1 = hstack((spdiags(np.sqrt(AAdiagT), 0, n, n), csc_matrix((n, k))))
P2 = hstack((csc_matrix((k, n)), S_chol))
P = vstack((P1, P2))
M = P @ P.T
print('P = ', P)
print('M = ', M)

# % minres with preconditioner:
sys.stdout = open('output1.txt', 'w')
print('Total ', 'fB ', 'sigma * fI ', 'kappa * fK ', 'C*alpha')
t1 = time.time()
#sol1, info1 = minres(A_operator, rhs, x0=np.zeros((n + k, 1)), tol=tol, maxiter=maxiter, callback=callback)
sol1, info1 = minres(A_operator, rhs, tol=tol, maxiter=1000, M=M, callback=callback)
t2 = time.time()
sys.stdout.close()
sys.stdout = open("/dev/stdout", "w")
t4 = t2 - t1
print('Second minres took = ', t4, ' s')
f1, fB1, fI1, fK1, fC1 = np.loadtxt('output1.txt', skiprows=1, unpack=True)
t1 = time.time()
#print(f1)

CCTinv = np.linalg.pinv((C @ C.T).todense(), rcond=1e-8, hermitian=True)
#CCTinv = np.linalg.pinv((C @ C.T), rcond=1e-8, hermitian=True)
print('Done computing CCTinv operator')

Proj = eye(n, format="csc", dtype="double") - C.T @ CCTinv @ C
xi = np.zeros((n, 1))
xi1 = np.zeros((n, 1))
AI_T = A_I.T
A1T = A1.T
shift = A1T @ b1 + AI_T @ b_I.reshape(1, 1)
max_iter = 500
print('Done computing proj operator')

#L = np.sqrt(n) * np.max(np.linalg.norm(A1T @ A1 + sigma * AI_T @ A_I + np.eye(n) * kappa_nu, axis=0), axis=-1)
L = np.linalg.svd(A1T @ A1 + sigma * AI_T @ A_I + np.eye(n) * kappa_nu, compute_uv=False, hermitian=True)[0]
step_i = 1 / L
t2 = time.time()
print('Done with SVD, setup took = ', t2 - t1, ' s')
f = np.zeros((max_iter, 2))
fB = np.zeros((max_iter, 2))
fI = np.zeros((max_iter, 2))
fK = np.zeros((max_iter, 2))
fC = np.zeros((max_iter, 2))
bnorm = np.linalg.norm(b)

t1 = time.time()
for i in range(max_iter):
    fB[i, 0] = np.linalg.norm(A1 @ xi - b1) ** 2 / scale ** 2
    fI[i, 0] = sigma * np.linalg.norm(A_I @ xi - b_I) ** 2 / scale ** 2
    fK[i, 0] = kappa * np.linalg.norm(xi) ** 2
    f[i, 0] = fB[i, 0] + fI[i, 0] + fK[i, 0] 
    fC[i, 0] = np.linalg.norm(C.dot(xi))
    xi = Proj.dot(xi + step_i * (shift - (A1T @ (A1 @ xi)) - sigma * (AI_T @ (A_I @ xi)) - kappa_nu * xi))
    # step_i = (1 + np.sqrt(1 + 4 * step_i ** 2)) / 2.0
    # print(i, np.linalg.norm(A @ xi - b) / bnorm)
    if (np.linalg.norm(A @ xi - b) / bnorm) < tol:
        f[:, 0] = f[f[:, 0] != 0.0, 0]
        fB[:, 0] = fB[fB[:, 0] != 0.0, 0]
        fI[:, 0] = fI[fI[:, 0] != 0.0, 0]
        fK[:, 0] = fK[fK[:, 0] != 0.0, 0]
        fC[:, 0] = fC[fC[:, 0] != 0.0, 0]
        break

t2 = time.time()
t5 = t2 - t1
print('Proj. grad descent took = ', t5, ' s')
t1 = time.time()
xi = np.zeros((n, 1))
xi1 = np.zeros((n, 1))

for i in range(max_iter):
    fB[i, 1] = np.linalg.norm(A1 @ xi - b1) ** 2 / scale ** 2
    fI[i, 1] = sigma * np.linalg.norm(A_I @ xi - b_I) ** 2 / scale ** 2
    fK[i, 1] = kappa * np.linalg.norm(xi) ** 2
    f[i, 1] = fB[i, 1] + fI[i, 1] + fK[i, 1] 
    fC[i, 1] = np.linalg.norm(C.dot(xi))
    # Do the updates
    vi = xi + i / (i + 3) * (xi - xi1)
    xi1 = xi
    xi = Proj.dot(vi + step_i * (shift - (A1T @ (A1 @ vi)) - sigma * (AI_T @ (A_I @ vi)) - kappa_nu * vi))
    step_i = (1 + np.sqrt(1 + 4 * step_i ** 2)) / 2.0
    if (np.linalg.norm(A @ xi - b) / bnorm) < tol:
        f[:, 1] = f[f[:, 1] != 0.0, 1]
        fB[:, 1] = fB[fB[:, 1] != 0.0, 1]
        fI[:, 1] = fI[fI[:, 1] != 0.0, 1]
        fK[:, 1] = fK[fK[:, 1] != 0.0, 1]
        fC[:, 1] = fC[fC[:, 1] != 0.0, 1]
        break

t2 = time.time()
t6 = t2 - t1
print('Nesterov took = ', t6, ' s')

xi = np.zeros((n, 1))
xi1 = np.zeros((n, 1))
contig = np.ascontiguousarray
A1T = contig(A1T)
A1 = contig(A1)
A_I = contig(A_I)
AI_T = contig(AI_T)
shift = contig(A1T @ b1 + AI_T @ b_I.reshape(1, 1))
t1 = time.time()

for i in range(max_iter):
    #fB[i, 1] = np.linalg.norm(A1 @ xi - b1) ** 2 / scale ** 2
    #fI[i, 1] = sigma * np.linalg.norm(A_I @ xi - b_I) ** 2 / scale ** 2
    #fK[i, 1] = kappa * np.linalg.norm(xi) ** 2
    #f[i, 1] = fB[i, 1] + fI[i, 1] + fK[i, 1] 
    #fC[i, 1] = np.linalg.norm(C.dot(xi))
    # Do the updates
    vi = contig(xi + i / (i + 3) * (xi - xi1))
    xi1 = xi
    xi = Proj.dot(vi + step_i * (shift - matmult(A1T, matmult(A1, vi)) - sigma * matmult(AI_T, matmult(A_I, vi)) - kappa_nu * vi))
    step_i = (1 + np.sqrt(1 + 4 * step_i ** 2)) / 2.0
    #if (np.linalg.norm(A @ xi - b) / bnorm) < tol:
    #    f[:, 1] = f[f[:, 1] != 0.0, 1]
    #    fB[:, 1] = fB[fB[:, 1] != 0.0, 1]
    #   fI[:, 1] = fI[fI[:, 1] != 0.0, 1]
    #    fK[:, 1] = fK[fK[:, 1] != 0.0, 1]
    #    fC[:, 1] = fC[fC[:, 1] != 0.0, 1]
    #    break

print(np.linalg.norm(A1 @ xi - b1) ** 2 / scale ** 2)
t2 = time.time()
t7 = t2 - t1
print('Nesterov in C++ took = ', t7, ' s')

# # % comparison of convergence
plt.figure(figsize=(20, 6))
plt.subplot(1, 6, 1)
plt.title('f total')
plt.loglog(f0, 'b')
plt.loglog(f1, 'c')
plt.loglog(f[:, 0], 'r')
plt.loglog(f[:, 1], 'm')
plt.legend(['MINRES', 'MINRES_precon', 'PGD', 'PGD_accel'])
plt.grid(True)
plt.subplot(1, 6, 2)
plt.title('fB')
plt.loglog(fB0, 'b')
plt.loglog(fB1, 'c') 
plt.loglog(fB[:, 0], 'r') 
plt.loglog(fB[:, 1], 'm') 
plt.legend(['MINRES', 'MINRES_precon', 'PGD', 'PGD_accel'])
plt.grid(True)
plt.subplot(1, 6, 3)
plt.title(r'$\sigma f_I$')
plt.loglog(fI0, 'b')
plt.loglog(fI1, 'c') 
plt.loglog(fI[:, 0], 'r') 
plt.loglog(fI[:, 1], 'm') 
plt.legend(['MINRES', 'MINRES_precon', 'PGD', 'PGD_accel'])
plt.grid(True)
plt.subplot(1, 6, 4)
plt.title(r'$\kappa f_K$')
plt.loglog(fK0, 'b')
plt.loglog(fK1, 'c') 
plt.loglog(fK[:, 0], 'r') 
plt.loglog(fK[:, 1], 'm') 
plt.legend(['MINRES', 'MINRES_precon', 'PGD', 'PGD_accel'])
plt.grid(True)
plt.subplot(1, 6, 5)
plt.title('Constraint satisfaction')
plt.loglog(fC0, 'b')
plt.loglog(fC1, 'c') 
plt.loglog(fC[:, 0], 'r') 
plt.loglog(fC[:, 1], 'm') 
plt.legend(['MINRES', 'MINRES_precon', 'PGD', 'PGD_accel'])
plt.grid(True)
plt.subplot(1, 6, 6)
plt.title('Total loss (incl. constraints)')
plt.loglog(f0 + fC0, 'b')
plt.loglog(f1 + fC1, 'c') 
plt.loglog(f[:, 0] + fC[:, 0], 'r') 
plt.loglog(f[:, 1] + fC[:, 1], 'm') 
plt.legend(['MINRES', 'MINRES_precon', 'PGD', 'PGD_accel'])
plt.grid(True)

# same plot with x-axis the number of seconds
t1 = np.linspace(0, t3, len(f0))
t2 = np.linspace(0, t4, len(f1))
t3 = np.linspace(0, t5, len(f[:, 0]))
t4 = np.linspace(0, t6, len(f[:, 1]))
plt.figure(figsize=(20, 6))
plt.subplot(1, 6, 1)
plt.title('f total')
plt.loglog(t1, f0, 'b')
plt.loglog(t2, f1, 'c')
plt.loglog(t3, f[:, 0], 'r')
plt.loglog(t4, f[:, 1], 'm')
plt.legend(['MINRES', 'MINRES_precon', 'PGD', 'PGD_accel'])
plt.grid(True)
plt.subplot(1, 6, 2)
plt.title('fB')
plt.loglog(t1, fB0, 'b')
plt.loglog(t2, fB1, 'c') 
plt.loglog(t3, fB[:, 0], 'r') 
plt.loglog(t4, fB[:, 1], 'm') 
plt.legend(['MINRES', 'MINRES_precon', 'PGD', 'PGD_accel'])
plt.grid(True)
plt.subplot(1, 6, 3)
plt.title(r'$\sigma f_I$')
plt.loglog(t1, fI0, 'b')
plt.loglog(t2, fI1, 'c') 
plt.loglog(t3, fI[:, 0], 'r') 
plt.loglog(t4, fI[:, 1], 'm') 
plt.legend(['MINRES', 'MINRES_precon', 'PGD', 'PGD_accel'])
plt.grid(True)
plt.subplot(1, 6, 4)
plt.title(r'$\kappa f_K$')
plt.loglog(t1, fK0, 'b')
plt.loglog(t2, fK1, 'c') 
plt.loglog(t3, fK[:, 0], 'r') 
plt.loglog(t4, fK[:, 1], 'm') 
plt.legend(['MINRES', 'MINRES_precon', 'PGD', 'PGD_accel'])
plt.grid(True)
plt.subplot(1, 6, 5)
plt.title('Constraint satisfaction')
plt.loglog(t1, fC0, 'b')
plt.loglog(t2, fC1, 'c') 
plt.loglog(t3, fC[:, 0], 'r') 
plt.loglog(t4, fC[:, 1], 'm') 
plt.legend(['MINRES', 'MINRES_precon', 'PGD', 'PGD_accel'])
plt.grid(True)
plt.subplot(1, 6, 6)
plt.title('Total loss (incl. constraints)')
plt.loglog(t1, f0 + fC0, 'b')
plt.loglog(t2, f1 + fC1, 'c') 
plt.loglog(t3, f[:, 0] + fC[:, 0], 'r') 
plt.loglog(t4, f[:, 1] + fC[:, 1], 'm') 
plt.legend(['MINRES', 'MINRES_precon', 'PGD', 'PGD_accel'])
plt.grid(True)
plt.show()


