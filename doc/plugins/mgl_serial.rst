====================================================
MGL Instruments RS-232 protocol
====================================================

Currently only tested with MGL Vega TP-3. Temperature and Pressure readings are assumed to be oil press and temp. 
Custom sensor type is unsupported at the moment.

Configuration
=========================

engine_no: specify for which engine the readings are

FUELQT ownership
=========================
A channel reported as fuel level is written to FUELQT. The shipped
``compute.yaml`` also writes FUELQT, by summing FUELQ1-FUELQ3. FIX keys have
one writer each, so when a fuel-level channel is wired, delete the compute
function whose output is FUELQT.
