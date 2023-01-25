#!/usr/bin/env python
r"""
This example uses the winding volume method 
outline in Kaptanoglu & Landreman 2023 in order
to make finite-build coils with no multi-filament
approximation. 

The script should be run as:
    mpirun -n 1 python winding_volume.py 

"""

import os
#from matplotlib import pyplot as plt
from pathlib import Path
import numpy as np
from scipy.sparse import csc_matrix
from scipy.sparse import vstack
from scipy.sparse import eye as sparse_eye
from scipy.sparse.linalg import inv as sparse_inv
# from sksparse.cholmod import cholesky
import simsoptpp as sopp
from simsopt.geo import SurfaceRZFourier, Curve, CurveRZFourier, curves_to_vtk
from simsopt.objectives import SquaredFlux
from simsopt.field.biotsavart import BiotSavart
from simsopt.field.magneticfieldclasses import WindingVolumeField
from simsopt.geo import WindingVolumeGrid
from simsopt.solve import projected_gradient_descent_Tikhonov 
from simsopt.util.permanent_magnet_helper_functions import *
import time

t_start = time.time()

# Set some parameters
nphi = 32  # nphi = ntheta >= 64 needed for accurate full-resolution runs
ntheta = 16
#dx = 0.1
#dy = dx
#dz = dx
Nx = 19
Ny = Nx
Nz = Nx - 1
poff = 0.3  # PM grid end offset ~ 10 cm from the plasma surface
coff = 0.1  # PM grid starts offset ~ 5 cm from the plasma surface
input_name = 'input.LandremanPaul2021_QA'

# Read in the plasma equilibrium file
TEST_DIR = (Path(__file__).parent / ".." / ".." / "tests" / "test_files").resolve()
surface_filename = TEST_DIR / input_name
s = SurfaceRZFourier.from_vmec_input(surface_filename, range="full torus", nphi=nphi, ntheta=ntheta)
# s = SurfaceRZFourier.from_vmec_input(surface_filename, range="half period", nphi=nphi, ntheta=ntheta)

qphi = nphi  # s.nfp * nphi * 2
quadpoints_phi = np.linspace(0, 1, qphi, endpoint=True)
quadpoints_theta = np.linspace(0, 1, ntheta, endpoint=True)
s_plot = SurfaceRZFourier.from_vmec_input(
    surface_filename, range="full torus",
    quadpoints_phi=quadpoints_phi, quadpoints_theta=quadpoints_theta
)

# Make the output directoryå
OUT_DIR = 'wv_QA/'
os.makedirs(OUT_DIR, exist_ok=True)

# No external coils
Bnormal = np.zeros((nphi, ntheta))

# Define a curve to define a Itarget loss term
# Make circle at Z = 0
numquadpoints = nphi * s.nfp * 2 * 5
order = 20
curve = CurveRZFourier(numquadpoints, order, nfp=1, stellsym=False)
for m in range(s.mpol + 1):
    if m == 0:
        nmin = 0
    else: 
        nmin = -s.ntor
    for n in range(nmin, s.ntor + 1):
        curve.rc[s.nfp * int(abs(n))] += s.get_rc(m, n)
        curve.zs[s.nfp * int(abs(n))] += s.get_zs(m, n) * np.sign(n)

curve.x = curve.get_dofs()
curve.x = curve.x  # need to do this to transfer data to C++
curves_to_vtk([curve], OUT_DIR + f"Itarget_curve")
Itarget = 1e6

nx = 10
# Finally, initialize the winding volume 
wv_grid = WindingVolumeGrid(
    s, Itarget_curve=curve, Itarget=Itarget, 
    coil_offset=coff, 
    Nx=Nx, Ny=Ny, Nz=Nz, 
    plasma_offset=poff,
    Bn=Bnormal,
    Bn_Itarget=np.zeros(curve.gammadash().reshape(-1, 3).shape[0]),
    filename=surface_filename,
    surface_flag='vmec',
    OUT_DIR=OUT_DIR,
    RANGE="full torus",
    nx=nx, ny=nx, nz=nx
)

wv_grid._toVTK(OUT_DIR + 'grid')

if True:
    t1 = time.time()
    C = wv_grid.flux_constraint_matrix  # matrix is way too big but it is very sparse
    # Need to append Itarget constraint to the flux jump constraints
    # C = vstack([C, wv_grid.Itarget_matrix], format="csc")
    CT = C.transpose()
    CCT = C @ CT

    # regularization required here to make this matrix
    # truly invertible. If not, can cause instability in the solver
    CCT += 1e-15 * np.eye(CCT.shape[0])
    t2 = time.time()
    print('Time to make CCT = ', t2 - t1, ' s')
    t1 = time.time()
    # factor = cholesky(CCT)
    # L = factor.L()
    # L_inv = sparse_inv(L)
    # LT_inv = sparse_inv(LT)

    #CCT_inv = sparse_inv(CCT)
    CCT_inv = np.linalg.inv(CCT)
    t2 = time.time()
    print('Time to make CCT_inv = ', t2 - t1, ' s')
    t1 = time.time()
    # CT_CCT_inv = CT @ CCT_inv
    # CT_CCT_inv_d = CT_CCT_inv[:, -1] * wv_grid.Itarget_rhs
    projection_onto_constraints = np.eye(wv_grid.N_grid * wv_grid.n_functions) - CT @ CCT_inv @ C 
    # projection_onto_constraints = sparse_eye(wv_grid.N_grid * wv_grid.n_functions, format="csc") - CT @ CCT_inv @ C 
    wv_grid.alphas = projection_onto_constraints.dot(np.ravel(wv_grid.alphas)).reshape(wv_grid.alphas.shape)
    t2 = time.time()
    print('Time to make projection operator and project alpha = ', t2 - t1, ' s')
    wv_grid._toVTK(OUT_DIR + 'grid_with_flux_jump_constraints')
else:
    projection_onto_constraints = None

nfp = wv_grid.plasma_boundary.nfp
print('fB initial = ', 0.5 * np.linalg.norm(wv_grid.B_matrix @ wv_grid.alphas - wv_grid.b_rhs, ord=2) ** 2 * nfp)
t1 = time.time()
lam = 1e-25
acceleration = True
max_iter = 20000
alpha_opt, fB, fK, fI = projected_gradient_descent_Tikhonov(wv_grid, lam=lam, P=projection_onto_constraints, acceleration=acceleration, max_iter=max_iter)
# print('alpha_opt = ', alpha_opt)
if projection_onto_constraints is not None:
    # print('P * alpha_opt - alpha_opt = ', projection_onto_constraints.dot(alpha_opt) - alpha_opt)
    print('||P * alpha_opt - alpha_opt|| / ||alpha_opt|| = ', np.linalg.norm(projection_onto_constraints.dot(alpha_opt) - alpha_opt) / np.linalg.norm(alpha_opt))
t2 = time.time()
print('Gradient Descent Tikhonov solve time = ', t2 - t1, ' s')
plt.figure()
plt.semilogy(fB, label='fB')
plt.semilogy(lam * fK, label='lam * fK')
plt.semilogy(fI, label='fI')
plt.semilogy(fB + fI + lam * fK, label='total')
plt.grid(True)
plt.legend()
plt.savefig(OUT_DIR + 'optimization_progress.jpg')
wv_grid.alphas = alpha_opt
wv_grid._toVTK(OUT_DIR + 'grid_after_Tikhonov_solve')
# print('fB after optimization = ', fB) 

# set up WindingVolume Bfield
bs_wv = WindingVolumeField(wv_grid)
bs_wv.set_points(s.gamma().reshape((-1, 3)))
Bnormal_wv = np.sum(bs_wv.B().reshape((nphi, ntheta, 3)) * s.unitnormal(), axis=2)
normN = np.linalg.norm(s.normal().reshape(-1, 3), axis=-1)
# print('Bnormal direct = ', Bnormal_wv)
# print('Bnormal lstsq = ', wv_grid.B_matrix @ alpha_opt * np.sqrt(nphi * ntheta) / np.sqrt(normN))
# print('Bnormal coils = ', Bnormal)
print('fB direct = ', np.sum(normN * np.ravel(Bnormal_wv + Bnormal) ** 2) * 0.5 / (nphi * ntheta))
# fB_direct = SquaredFlux(s, bs_wv, -Bnormal).J()
# print('fB_direct = ', fB_direct)

make_Bnormal_plots(bs_wv, s_plot, OUT_DIR, "biot_savart_winding_volume")
# make_Bnormal_plots(bs + bs_wv, s, OUT_DIR, "biot_savart_total")

wv_grid.check_fluxes()

t_end = time.time()
print('Total time = ', t_end - t_start)
plt.show()
