"""Implements the force on a coil in its own magnetic field and the field of other coils."""
import numpy as np
from scipy import constants
import jax.numpy as jnp
from jax import grad, jit
from .biotsavart import BiotSavart
from .selffield import B_regularized_pure, B_regularized, regularization_circ, regularization_rect
from .._core.optimizable import Optimizable
from .._core.derivative import derivative_dec

Biot_savart_prefactor = constants.mu_0 / 4 / np.pi

def coil_force_pure(B, I, t):
    """force on coil for optimization"""
    return jnp.cross(I * t, B)

def self_force(coil, regularization):
    """
    Compute the self-force of a coil.
    """
    I = coil.current.get_value()
    tangent = coil.curve.gammadash() / np.linalg.norm(coil.curve.gammadash(),axis=1)[:, None]
    B = B_regularized(coil, regularization)
    return coil_force_pure(B, I, tangent)

def self_force_circ(coil, a):
    """Compute the Lorentz self-force of a coil with circular cross-section"""
    return self_force(coil, regularization_circ(a))

def self_force_rect(coil, a, b):
    """Compute the Lorentz self-force of a coil with rectangular cross-section"""
    return self_force(coil, regularization_rect(a, b))

@jit
def mean_squared_force_pure(gamma, gammadash, gammadashdash, quadpoints, current, regularization, B_mutual):
    r"""Pure function for minimizing the Lorentz force on a coil.
    
    The function is

    .. math:
        J = \frac{\int |\vec{F}|^2 d\ell}{\int d\ell}

    where :math:`\vec{F}` is the Lorentz force and :math:`\ell` is arclength
    along the coil.
    """
    B_self = B_regularized_pure(gamma, gammadash, gammadashdash, quadpoints, current, regularization)
    gammadash_norm = jnp.linalg.norm(gammadash, axis=1)[:, None]
    tangent = gammadash / gammadash_norm
    force = jnp.cross(current * tangent, B_self + B_mutual)
    force_norm = jnp.linalg.norm(force, axis=1)[:, None]
    return jnp.sum(gammadash_norm * force_norm**2) / jnp.sum(gammadash_norm)


class MeanSquaredForce(Optimizable):
    r"""Optimizable class to minimize the Lorentz force on a coil.
    
    The objective function is

    .. math:
        J = \frac{\int |\vec{F}|^2 d\ell}{\int d\ell}

    where :math:`\vec{F}` is the Lorentz force and :math:`\ell` is arclength
    along the coil.
    """
    def __init__(self, this_coil, all_coils, regularization):
        self.this_coil = this_coil
        self.other_coils = [c for c in all_coils if c is not this_coil]
        self.regularization = regularization
        quadpoints = this_coil.curve.quadpoints
        self.biot_savart = BiotSavart(self.other_coils)

        self.J_jax = jit(
            lambda gamma, gammadash, gammadashdash, current, B_mutual:
            mean_squared_force_pure(gamma, gammadash, gammadashdash, quadpoints, current, regularization, B_mutual)
        )

        self.dJ_dgamma = jit(
            lambda gamma, gammadash, gammadashdash, current, B_mutual:
            grad(self.J_jax, argnums=0)(gamma, gammadash, gammadashdash, current, B_mutual)
        )

        self.dJ_dgammadash = jit(
            lambda gamma, gammadash, gammadashdash, current, B_mutual:
            grad(self.J_jax, argnums=1)(gamma, gammadash, gammadashdash, current, B_mutual)
        )

        self.dJ_dgammadashdash = jit(
            lambda gamma, gammadash, gammadashdash, current, B_mutual:
            grad(self.J_jax, argnums=2)(gamma, gammadash, gammadashdash, current, B_mutual)
        )

        self.dJ_dcurrent = jit(
            lambda gamma, gammadash, gammadashdash, current, B_mutual:
            grad(self.J_jax, argnums=3)(gamma, gammadash, gammadashdash, current, B_mutual)
        )

        self.dJ_dB_mutual = jit(
            lambda gamma, gammadash, gammadashdash, current, B_mutual:
            grad(self.J_jax, argnums=4)(gamma, gammadash, gammadashdash, current, B_mutual)
        )

        super().__init__(depends_on=all_coils)  # Should this be all curves or just the base curves?

    def J(self):
        self.biot_savart.set_points(self.this_coil.curve.gamma())
        return self.J_jax(
            self.this_coil.curve.gamma(),
            self.this_coil.curve.gammadash(),
            self.this_coil.curve.gammadashdash(),
            self.this_coil.current.get_value(),
            self.biot_savart.B()
        )

    @derivative_dec
    def dJ(self):
        self.biot_savart.set_points(self.this_coil.curve.gamma())
        args = [
            self.this_coil.curve.gamma(),
            self.this_coil.curve.gammadash(),
            self.this_coil.curve.gammadashdash(),
            self.this_coil.current.get_value(),
            self.biot_savart.B()
        ]
        return (
            self.this_coil.curve.dgamma_by_dcoeff_vjp(self.dJ_dgamma(*args))
            + self.this_coil.curve.dgammadash_by_dcoeff_vjp(self.dJ_dgammadash(*args))
            + self.this_coil.curve.dgammadashdash_by_dcoeff_vjp(self.dJ_dgammadashdash(*args))
            + self.this_coil.current.vjp(self.dJ_dcurrent(*args))
            # Add terms for d / d B_mutual here
        )

    return_fn_map = {'J': J, 'dJ': dJ}
