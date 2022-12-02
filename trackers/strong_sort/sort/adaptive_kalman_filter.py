# vim: expandtab:ts=4:sw=4
import time
import numpy as np
import scipy.linalg
"""
Table for the 0.95 quantile of the chi-square distribution with N degrees of
freedom (contains values for N=1, ..., 9). Taken from MATLAB/Octave's chi2inv
function and used as Mahalanobis gating threshold.
"""
chi2inv95 = {
    1: 3.8415,
    2: 5.9915,
    3: 7.8147,
    4: 9.4877,
    5: 11.070,
    6: 12.592,
    7: 14.067,
    8: 15.507,
    9: 16.919}


class AdaptiveKalmanFilter(object):
    """
    An adaptive Kalman filter for tracking bounding boxes in image space.
    The 8-dimensional state space
        x, y, a, h, vx, vy, va, vh
    contains the bounding box center position (x, y), aspect ratio a, height h,
    and their respective velocities.
    Object motion follows a constant velocity model. The bounding box location
    (x, y, a, h) is taken as direct observation of the state space (linear
    observation model).

    Adaptive method for estimating Q and R implemented based on:
    https://arxiv.org/ftp/arxiv/papers/1702/1702.00884.pdf
    """

    def __init__(self, ):
        self.ndim, dt = 4, 1.
        self._I = np.eye(2 * self.ndim)

        # Create Kalman filter model matrices.
        self._motion_mat = np.eye(2 * self.ndim, 2 * self.ndim)
        for i in range(self.ndim):
            self._motion_mat[i, self.ndim + i] = dt

        self._update_mat = np.eye(self.ndim, 2 * self.ndim)

        # Motion and observation uncertainty are chosen relative to the current
        # state estimate. These weights control the amount of uncertainty in
        # the model. This is a bit hacky.
        self._std_weight_position = 1. / 20
        self._std_weight_velocity = 1. / 160
        self._std_weight_accel = 1. / 160

        self._forgetting_factor = 0.9

        self._process_noise = np.zeros((8, 8))
        self._measurement_noise = np.zeros((4, 4))
        self._residual = np.zeros((4,))
        
        self.image_width = 0
        self.image_height = 0

    
    @property
    def process_noise(self):
        return self._process_noise
        
    @process_noise.setter
    def process_noise(self, val):
        self._process_noise = val

    @property
    def measurement_noise(self):
        return self._measurement_noise
    
    @measurement_noise.setter
    def measurement_noise(self, val):
        self._measurement_noise = val

    def distance_to_image_center(self, bbox):
        return np.sqrt((self.image_width / 2 - bbox[0]) ** 2 + (self.image_height / 2 - bbox[1]) ** 2)

    def initiate(self, measurement):
        """Create track from unassociated measurement.
        Parameters
        ----------
        measurement : ndarray
            Bounding box coordinates (x, y, a, h) with center position (x, y),
            aspect ratio a, and height h.
        Returns
        -------
        (ndarray, ndarray)
            Returns the mean vector (8 dimensional) and covariance matrix (8x8
            dimensional) of the new track. Unobserved velocities are initialized
            to 0 mean.
        """
        mean_pos = measurement
        mean_vel = np.zeros_like(mean_pos)
        mean = np.r_[mean_pos, mean_vel]
        
        dist = self.distance_to_image_center(measurement)

        std = [
            2 * self._std_weight_position * dist,   # the center point x
            2 * self._std_weight_position * dist,   # the center point y
            1 * measurement[2],                               # the ratio of width/height
            2 * self._std_weight_position * measurement[3],   # the height
            10 * self._std_weight_velocity * dist,
            10 * self._std_weight_velocity * dist,
            0.1 * measurement[2],
            10 * self._std_weight_velocity * measurement[3]
        ]
        covariance = np.diag(np.square(std))

        """self._measurement_noise = np.diag([
            self._std_weight_position * mean[3],
            self._std_weight_position * mean[3],
            1e-1,
            self._std_weight_position * mean[3]
        ])

        std_pos = [
            self._std_weight_position * mean[0],
            self._std_weight_position * mean[1],
            1 * mean[2],
            self._std_weight_position * mean[3]
        ]
        std_vel = [
            self._std_weight_velocity * mean[0],
            self._std_weight_velocity * mean[1],
            0.1 * mean[2],
            self._std_weight_velocity * mean[3]
        ]
        self._process_noise = np.diag(np.square(np.r_[std_pos, std_vel]))"""

        return mean, covariance

    def predict(self, mean, covariance):
        """Run Kalman filter prediction step.
        Parameters
        ----------
        mean : ndarray
            The 8 dimensional mean vector of the object state at the previous
            time step.
        covariance : ndarray
            The 8x8 dimensional covariance matrix of the object state at the
            previous time step.
        Returns
        -------
        (ndarray, ndarray)
            Returns the mean vector and covariance matrix of the predicted
            state. Unobserved velocities are initialized to 0 mean.
        """

        mean = np.dot(self._motion_mat, mean)
        covariance = np.linalg.multi_dot((
            self._motion_mat, covariance, self._motion_mat.T)) + self._process_noise

        return mean, covariance

    def project(self, mean, covariance, confidence=.0):
        """Project state distribution to measurement space.
        Parameters
        ----------
        mean : ndarray
            The state's mean vector (8 dimensional array).
        covariance : ndarray
            The state's covariance matrix (8x8 dimensional).
        confidence: (dyh) 检测框置信度
        Returns
        -------
        (ndarray, ndarray)
            Returns the projected mean and covariance matrix of the given state
            estimate.
        """
        projected_mean = np.dot(self._update_mat, mean)
        

        projected_cov = np.linalg.multi_dot((
            self._update_mat, covariance, self._update_mat.T))

        # Estimate measurement noise based on a priori residual and projected
        # covariance
        std = [
            self._std_weight_position * mean[3],
            self._std_weight_position * mean[3],
            1e-1,
            self._std_weight_position * mean[3]]

        std = [(1 - confidence) * x for x in std]
        additive_measurement_noise = np.diag(np.square(std))
        
        est_measurement_noise = np.outer(self._residual, self._residual) + projected_cov
        self._measurement_noise = (
                self._forgetting_factor * (self._measurement_noise + additive_measurement_noise)
                + (1 - self._forgetting_factor) * est_measurement_noise
        )

        # Update projected covariance with adapted measurement noise
        projected_cov += self._measurement_noise
        
        return projected_mean, projected_cov

    def update(self, mean, covariance, measurement, confidence=.0):
        """Run Kalman filter correction step.
        Parameters
        ----------
        mean : ndarray
            The predicted state's mean vector (8 dimensional).
        covariance : ndarray
            The state's covariance matrix (8x8 dimensional).
        measurement : ndarray
            The 4 dimensional measurement vector (x, y, a, h), where (x, y)
            is the center position, a the aspect ratio, and h the height of the
            bounding box.
        confidence: (dyh)检测框置信度
        Returns
        -------
        (ndarray, ndarray)
            Returns the measurement-corrected state distribution.
        """
        projected_mean, projected_cov = self.project(mean, covariance, confidence)

        innovation = measurement - projected_mean

        # Calculate Kalman gain
        chol_factor, lower = scipy.linalg.cho_factor(
            projected_cov, lower=True, check_finite=False)
        kalman_gain = scipy.linalg.cho_solve(
            (chol_factor, lower), np.dot(covariance, self._update_mat.T).T,
            check_finite=False).T

        # Estimate a posteriori process noise based on Kalman gain
        # and innovation
        dist = self.distance_to_image_center(mean)
        std_pos = [
            self._std_weight_position * dist,
            self._std_weight_position * dist,
            1 * mean[2],
            self._std_weight_position * mean[3]]
        std_vel = [
            self._std_weight_velocity * dist,
            self._std_weight_velocity * dist,
            0.1 * mean[2],
            self._std_weight_velocity * mean[3]]
        additive_process_noise = np.diag(np.square(np.r_[std_pos, std_vel]))
        
        est_process_noise = np.linalg.multi_dot((
            kalman_gain, np.outer(innovation, innovation), kalman_gain.T))
        self._process_noise = (
                self._forgetting_factor * (self._process_noise + additive_process_noise)
                + (1 - self._forgetting_factor) * est_process_noise
        )

        new_mean = mean + np.dot(innovation, kalman_gain.T)
        
        # The following way of updating the state covariance is more numerically
        # stable than the text book equation P' = (I-KH)P
        I_KH = self._I - np.dot(kalman_gain, self._update_mat)
        new_covariance = (
            np.linalg.multi_dot((I_KH, covariance, I_KH.T))
            + np.linalg.multi_dot((kalman_gain, self._measurement_noise, kalman_gain.T))
        )

        upd_projected_mean = np.dot(self._update_mat, new_mean)
        self._residual = measurement - upd_projected_mean

        return new_mean, new_covariance

    def gating_distance(self, mean, covariance, measurements,
                        only_position=False):
        """Compute gating distance between state distribution and measurements.
        A suitable distance threshold can be obtained from `chi2inv95`. If
        `only_position` is False, the chi-square distribution has 4 degrees of
        freedom, otherwise 2.
        Parameters
        ----------
        mean : ndarray
            Mean vector over the state distribution (8 dimensional).
        covariance : ndarray
            Covariance of the state distribution (8x8 dimensional).
        measurements : ndarray
            An Nx4 dimensional matrix of N measurements, each in
            format (x, y, a, h) where (x, y) is the bounding box center
            position, a the aspect ratio, and h the height.
        only_position : Optional[bool]
            If True, distance computation is done with respect to the bounding
            box center position only.
        Returns
        -------
        ndarray
            Returns an array of length N, where the i-th element contains the
            squared Mahalanobis distance between (mean, covariance) and
            `measurements[i]`.
        """
        projected_mean, projected_cov = self.project(mean, covariance)

        if only_position:
            projected_mean, projected_cov = projected_mean[:2], projected_cov[:2, :2]
            measurements = measurements[:, :2]

        cholesky_factor = np.linalg.cholesky(projected_cov)
        d = measurements - projected_mean
        z = scipy.linalg.solve_triangular(
            cholesky_factor, d.T, lower=True, check_finite=False,
            overwrite_b=True)
        squared_maha = np.sum(z * z, axis=0)
        return squared_maha