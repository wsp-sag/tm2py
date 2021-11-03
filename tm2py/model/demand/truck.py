"""Truck model module. See class for documentation.
"""


import numpy as np
import openmatrix as omx
import os
import pandas as pd

from tm2py.core.component import Component as _Component, Controller as _Controller
import tm2py.core.emme as _emme_tools
from tm2py.core.logging import LogStartEnd


# employment category mappings, grouping into larger categories
_land_use_aggregation = {
    "AGREMPN": ["ag"],
    "RETEMPN": ["ret_loc", "ret_reg"],
    "FPSEMPN": ["fire", "info", "lease", "prof", "serv_bus"],
    "HEREMPN": [
        "art_rec",
        "eat",
        "ed_high",
        "ed_k12",
        "ed_oth",
        "health",
        "hotel",
        "serv_pers",
        "serv_soc",
    ],
    "MWTEMPN": [
        "logis",
        "man_bio",
        "man_hvy",
        "man_lgt",
        "man_tech",
        "natres",
        "transp",
        "util",
    ],
    "OTHEMPN": ["constr", "gov"],
    "TOTEMP": ["emp_total"],
    "TOTHH": ["HH"],
}
_time_of_day_split = {
    "ea": {"vsmtrk": 0.0235, "smltrk": 0.0765, "medtrk": 0.0665, "lrgtrk": 0.1430},
    "am": {"vsmtrk": 0.0700, "smltrk": 0.2440, "medtrk": 0.2930, "lrgtrk": 0.2320},
    "md": {"vsmtrk": 0.6360, "smltrk": 0.3710, "medtrk": 0.3935, "lrgtrk": 0.3315},
    "pm": {"vsmtrk": 0.1000, "smltrk": 0.2180, "medtrk": 0.1730, "lrgtrk": 0.1750},
    "ev": {"vsmtrk": 0.1705, "smltrk": 0.0905, "medtrk": 0.0740, "lrgtrk": 0.1185},
}
# True to reference old names generated by cube assignments, False to use Emme 
# naming and structure. For testing only, to be removed.
use_old_skims = False


class TruckModel(_Component):
    """Truck demand model generates demand for 4 sizes of truck, toll and nontoll by time of day.

    The four truck types are: very small trucks (two-axle, four-tire), 
    small trucks (two-axle, six-tire), medium trucks (three-axle), 
    and large or combination (four or more axle) trucks.

    TODO: doc config

    Input:  (1) MAZ csv data file with the employment and household counts.
            (2) highway skims for truck, time, distance, bridgetoll and value toll
            (3) friction factors lookup table
            (4) k-factors matrix
    Ouput:  Trips by time-of-day for 4 truck sizes X 2 types, toll and nontoll

    Notes: 
    (1) Based on the BAYCAST truck model, no significant updates.  
    (2) Combined Chuck's calibration adjustments into the NAICS-based model coefficients.

    Trip generation
    ---------------
    Use linear regression models to generate trip ends,
    balancing attractions to productions. Based on BAYCAST truck model.

    The truck trip generation models for small trucks (two-axle, six tire),
    medium trucks (three-axle), and large or combination (four or more axle)
    trucks are taken directly from the study: "I-880 Intermodal Corridor Study:
    Truck Travel in the San Francisco Bay Area", prepared by Barton Aschman in
    December 1992.  The coefficients are on page 223 of this report.

    The very small truck generation model is based on the Phoenix four-tire
    truck model documented in the TMIP Quick Response Freight Manual.

    Note that certain production models previously used SIC-based employment
    categories.  To both maintain consistency with the BAYCAST truck model and
    update the model to use NAICS-based employment categories, new regression
    models were estimated relating the NAICS-based employment data with the
    SIC-based-predicted trips.  The goal here is not to create a new truck
    model, but to mimic the old model with the available data.  Please see
    the excel spreadsheet TruckModel.xlsx for details.  The NAICS-based model
    results replicate the SIC-based model results quite well.



    Trip distribution
    -----------------
    A simple gravity model is used to distribute the truck trips, with 
    separate friction factors used for each class of truck.  

 A blended travel time is used as the impedance measure, specifically the weighted average of the AM travel time
 (one-third weight) and the midday travel time (two-thirds weight). 

 Input:  (1) Level-of-service matrices for the AM peak period (6 am to 10 am) and midday period (10 am to 3 pm)
             which contain truck-class specific estimates of congested travel time (in minutes) using the following
             table names:(a) timeVSM, which is the time for very small trucks; (b) timeSML, which is the time for 
             small trucks; (c) timeMED, which is the time for medium trucks; and, (d) timeLRG, which is the time
             for large trucks.
         (2) Trip generation results in ASCII format with the following fields (each 12 columns wide): (a) zone 
             number; (b) very small truck trip productions; (c) very small truck trip attractions; (d) small truck
             trip productions; (e) small truck trip attractions; (f) medium truck trip productions; (g) medium 
             truck trip attractions; (h) large truck trip productions; and, (i) large truck trip attractions. 
         (3) A matrix of k-factors, as calibrated by Chuck Purvis.  Note the very small truck model does not use
             k-factors; the small, medium, and large trucks use the same k-factors. 
         (4) A table of friction factors in ASCII format with the following fields (each 12 columns wide): (a)
             impedance measure (blended travel time); (b) friction factors for very small trucks; (c) friction
             factors for small trucks; (d) friction factors for medium trucks; and, (e) friction factors for large
             trucks. 

 Output: (1) A four-table production/attraction trip table matrix of daily class-specific truck trips (in units 
             of trips x 100, to be consistent with the previous application)with a table for (a) very small trucks,
             (b) small trucks, (c) medium trucks, and (d) large trucks.

 Notes:  (1) These scripts do not update the BAYCAST truck model; rather, the model is simply implemented in a
             manner consistent with the Travel Model One implementation. 

 See also: (1) TruckTripGeneration.job, which applies the generation model.
           (2) TruckTimeOfDay.job, which applies diurnal factors to the daily trips generated here. 
           (3) TruckTollChoice.job, which applies a toll/no toll choice model for trucks.



TruckTimeOfDay.job

 TP+ script to segment daily estimates of truck flows into time-period-specific flows.  The time periods are: 
 early AM, 3 am to 6 am; AM peak, 6 am to 10 am; midday, 10 am to 3 pm; PM peak, 3 pm to 7 pm; and evening, 
 7 pm to 3 am the next day. The four truck types are: very small trucks (two-axle, four-tire), small trucks 
 (two-axle, six-tire), medium trucks (three-axle), and large or combination (four or more axle) trucks.

 The diurnal factors are taken from the BAYCAST-90 model with adjustments made during calibration to the very
 small truck values to better match counts. 

 Input:   A four-table production/attraction trip table matrix of daily class-specific truck trips (in units 
          of trips x 100, to be consistent with the previous application)with a table for (a) very small trucks,
          (b) small trucks, (c) medium trucks, and (d) large trucks.

 Output: Five, time-of-day-specific trip table matrices, each containing the following four tables: (a) vstruck,
         for very small trucks, (b) struck, for small trucks, (c) mtruck, for medium trucks, and (d) ctruck,
         for combination truck. 

 Notes:  (1) These scripts do not update the BAYCAST truck model; rather, the model is simply implemented in a
             manner consistent with the Travel Model One implementation



TruckTollChoice.job

 TP+ script to apply a binomial choice model for very small, small, medium, and large trucks.  Two loops are used.
 The first cycles through the five time periods and the second cycles through the four types of commercial vehicles.
 The time periods are: (a) early AM, before 6 am; (b) AM peak period, 7 am to 10 am; (c) midday, 10 am to 3 pm; 
 (d) PM peak period, 3 pm to 7 pm; and, (e) evening, after 7 pm.  The four types of commercial vehicles are: 
 very small, small, medium, and large.  A separate value toll paying versus no value toll paying path choice
 model is applied to each of the twenty time period/vehicle type combinations.

 Input:  (1) Origin/destination matrix of very small, small, medium, and large truck trips
         (2) Skims providing the time and cost for value toll and non-value toll paths for each; the tables must
             have the following names:
             (a) Non-value-toll paying time: TIMEXXX;
            (b) Non-value-toll distance: DISTXXX
             (c) Non-value-toll bridge toll is: BTOLLXXX;
             (d) Value-toll paying time is: TOLLTIMEXXX;
             (e) Value-toll paying distance is: TOLLDISTXXX;
         (f) Value-toll bridge toll is: TOLLBTOLLXXX;
         (g) Value-toll value toll is: TOLLVTOLLXXX,
          where XXX is VSM, SML, MED, or LRG (vehicle type).

 Output: Five, eight-table trip tables.  One trip table for each time period.  Two tables for each vehicle class
         representing value-toll paying path trips and non-value-toll paying path trips. 

 Notes:  (1)  TOLLCLASS is a code, 1 through 10 are reserved for bridges; 11 and up is reserved for value toll
              facilities. 
         (2)  All costs should be coded in year 2000 cents
         (3)  The 2-axle fee is used for very small trucks
         (4)  The 2-axle fee is used for small trucks
         (5)  The 3-axle fee is used for medium trucks
         (6)  The average of the 5-axle and 6-axle fee is used for large trucks (about the midpoint of the fee
              schedule).
         (7)  The in-vehicle time coefficient is taken from the work trip mode choice model. 
    """

    def __init__(self, controller: _Controller):
        super().__init__(controller)
        self._num_processors = _emme_tools.parse_num_processors(
            self.config.emme.num_processors
        )
        self._emme_manager = None
        self._scenario = None

    @LogStartEnd()
    def run(self):
        """Run truck sub-model to generate assignable truck class demand."""
        # future note: should not round intermediate results
        # future note: could use skim matrix cache from assignment
        self._setup_emme()
        taz_landuse = self._aggregate_landuse()
        trip_ends = self._generation(taz_landuse)
        daily_demand = self._distribution(trip_ends)
        period_demand = self._time_of_day(daily_demand)
        class_demands = self._toll_choice(period_demand)
        self._export_results(class_demands)

    def _setup_emme(self):
        """Start Emme desktop session and create matrices for balancing."""
        self._emme_manager = _emme_tools.EmmeManager()
        project_path = os.path.join(self.root_dir, self.config.emme.project_path)
        project = self._emme_manager.project(project_path)
        self._emme_manager.init_modeller(project)
        # Note: using the highway assignment Emmebank by path
        emmebank = self._emme_manager.emmebank(
            os.path.join(self.root_dir, self.config.emme.highway_database_path)
        )
        # use first valid scenario for reference Zone IDs
        ref_scenario_id = self.config.periods[0].emme_scenario_id
        self._scenario = emmebank.scenario(ref_scenario_id)
        # matrix names, internal to this class and Emme database
        # (used in _matrix_balancing method)
        # NOTE: could use temporary matrices (delete when finished)
        matrices = {
            "FULL": [
                ("vsmtrk_friction", "very small truck friction factors"),
                ("smltrk_friction", "small truck friction factors"),
                ("medtrk_friction", "medium truck friction factors"),
                ("lrgtrk_friction", "large truck friction factors"),
                ("vsmtrk_daily_demand", "very small truck daily demand"),
                ("smltrk_daily_demand", "small truck daily demand"),
                ("medtrk_daily_demand", "medium truck daily demand"),
                ("lrgtrk_daily_demand", "large truck daily demand"),
            ],
            "ORIGIN": [
                ("vsmtrk_prod", "very small truck daily productions"),
                ("smltrk_prod", "small truck daily productions"),
                ("medtrk_prod", "medium truck daily productions"),
                ("lrgtrk_prod", "large truck daily productions"),
            ],
            "DESTINATION": [
                ("vsmtrk_attr", "very small truck daily attractions"),
                ("smltrk_attr", "small truck daily attractions"),
                ("medtrk_attr", "medium truck daily attractions"),
                ("lrgtrk_attr", "large truck daily attractions"),
            ],
        }
        for matrix_type, matrix_names in matrices.items():
            for name, desc in matrix_names:
                matrix = emmebank.matrix(name)
                if not matrix:
                    ident = emmebank.available_matrix_identifier(matrix_type)
                    matrix = emmebank.create_matrix(ident)
                    matrix.name = name
                matrix.description = desc

    @LogStartEnd()
    def _aggregate_landuse(self):
        """Aggregates landuse data from input CSV by MAZ to TAZ and employment groups.
        TOTEMP, total employment (same regardless of classification system)
        RETEMPN, retail trade employment per the NAICS classification system
        FPSEMPN, financial and professional services employment per NAICS
        HEREMPN, health, educational, and recreational employment per  NAICS
        OTHEMPN, other employment per the NAICS classification system
        AGREMPN, agricultural employment per the NAICS classificatin system
        MWTEMPN, manufacturing, warehousing, and transportation employment per NAICS
        TOTHH, total households
        """
        maz_data_file = os.path.join(self.root_dir, self.config.scenario.maz_landuse_file)
        maz_input_data = pd.read_csv(maz_data_file)
        taz_input_data = maz_input_data.groupby(["TAZ_ORIGINAL"]).sum()
        # TODO: double check comes back sorted by TAZ ID
        # taz_input_data = taz_input_data.sort_values(by="TAZ")
        # combine categories
        taz_landuse = pd.DataFrame()
        for total_column, sub_categories in _land_use_aggregation.items():
            taz_landuse[total_column] = taz_input_data[
                [category for category in sub_categories]
            ].sum(axis=1)
        taz_landuse.reset_index(inplace=True)
        return taz_landuse

    def _generation(self, landuse):
        """Run truck trip generation on input landuse dataframe.

        This step applies simple generation models, balances attractions
        and productions, sums linked and unlinked trips and returns vectors
        of production and attactions as a pandas dataframe.

        Expected columns for landuse are: AGREMPN, RETEMPN, FPSEMPN, HEREMPN,
        MWTEMPN, OTHEMPN, TOTEMP, TOTHH

        Returned columns are: vsmtrk_prod, vsmtrk_attr, smltrk_prod,
        smltrk_attr, medtrk_prod, medtrk_attr, lrgtrk_prod, lrgtrk_attr
        """

        link_trips = pd.DataFrame()
        # linked trips (non-garage-based) - productions
        # (very small updated with NAICS coefficients)
        link_trips["vsmtrk_prod"] = (
            0.96
            * (
                0.95409 * landuse.RETEMPN
                + 0.54333 * landuse.FPSEMPN
                + 0.50769 * landuse.HEREMPN
                + 0.63558 * landuse.OTHEMPN
                + 1.10181 * landuse.AGREMPN
                + 0.81576 * landuse.MWTEMPN
                + 0.26565 * landuse.TOTHH
            )
        ).round(decimals=0)
        link_trips["smltrk_prod"] = (0.0324 * landuse.TOTEMP).round()
        link_trips["medtrk_prod"] = (0.0039 * landuse.TOTEMP).round()
        link_trips["lrgtrk_prod"] = (0.0073 * landuse.TOTEMP).round()
        # linked trips (non-garage-based) - attractions (equal productions)
        link_trips["vsmtrk_attr"] = link_trips["vsmtrk_prod"]
        link_trips["smltrk_attr"] = link_trips["smltrk_prod"]
        link_trips["medtrk_attr"] = link_trips["medtrk_prod"]
        link_trips["lrgtrk_attr"] = link_trips["lrgtrk_prod"]
        garage_trips = pd.DataFrame()
        # garage-based - productions (updated NAICS coefficients)
        garage_trips["smltrk_prod"] = (
            0.02146 * landuse.RETEMPN
            + 0.02424 * landuse.FPSEMPN
            + 0.01320 * landuse.HEREMPN
            + 0.04325 * landuse.OTHEMPN
            + 0.05021 * landuse.AGREMPN
            + 0.01960 * landuse.MWTEMPN
        ).round()
        garage_trips["medtrk_prod"] = (
            0.00102 * landuse.RETEMPN
            + 0.00147 * landuse.FPSEMPN
            + 0.00025 * landuse.HEREMPN
            + 0.00331 * landuse.OTHEMPN
            + 0.00445 * landuse.AGREMPN
            + 0.00165 * landuse.MWTEMPN
        ).round()
        garage_trips["lrgtrk_prod"] = (
            0.00183 * landuse.RETEMPN
            + 0.00482 * landuse.FPSEMPN
            + 0.00274 * landuse.HEREMPN
            + 0.00795 * landuse.OTHEMPN
            + 0.01125 * landuse.AGREMPN
            + 0.00486 * landuse.MWTEMPN
        ).round()
        # garage-based - attractions
        garage_trips["smltrk_attr"] = (0.0234 * landuse.TOTEMP).round()
        garage_trips["medtrk_attr"] = (0.0046 * landuse.TOTEMP).round()
        garage_trips["lrgtrk_attr"] = (0.0136 * landuse.TOTEMP).round()
        # balance attractions to productions (applies to garage trips only)
        for name in ["smltrk", "medtrk", "lrgtrk"]:
            total_attract = garage_trips[name + "_attr"].sum()
            total_prod = garage_trips[name + "_prod"].sum()
            garage_trips[name + "_attr"] = garage_trips[name + "_attr"] * (
                total_prod / total_attract
            )

        trip_ends = pd.DataFrame(
            {
                "vsmtrk_prod": link_trips["vsmtrk_prod"],
                "vsmtrk_attr": link_trips["vsmtrk_attr"],
                "smltrk_prod": link_trips["smltrk_prod"] + garage_trips["smltrk_prod"],
                "smltrk_attr": link_trips["smltrk_attr"] + garage_trips["smltrk_attr"],
                "medtrk_prod": link_trips["medtrk_prod"] + garage_trips["medtrk_prod"],
                "medtrk_attr": link_trips["medtrk_attr"] + garage_trips["medtrk_attr"],
                "lrgtrk_prod": link_trips["lrgtrk_prod"] + garage_trips["lrgtrk_prod"],
                "lrgtrk_attr": link_trips["lrgtrk_attr"] + garage_trips["lrgtrk_attr"],
            }
        )
        trip_ends.round(decimals=7)
        return trip_ends

    @LogStartEnd()
    def _distribution(self, trip_ends):
        """Run trip distribution model for 4 truck types using Emme matrix balancing."""
        # input: the production / attraction vectors
        # load nonres\truck_kfactors_taz.csv
        # load nonres\truckFF.dat
        # Apply friction factors and kfactors to produce balancing matrix
        # apply the gravity models using friction factors from nonres\truckFF.dat
        # (note the very small trucks do not use the K-factors)
        #     Can use Emme matrix balancing for this - important note: reference
        #     matrices by name and ensure names are unique
        #     May want to use temporary matrices
        #     See core/emme.py
        # round trips to 0.01
        # return the daily truck trip matrices

        # load skims from OMX file
        # note that very small, small and medium are assigned as one class
        # and share the same output skims

        skim_path_tmplt = os.path.join(self.root_dir, self.config.highway.output_skim_path)
        with _emme_tools.OMX(skim_path_tmplt.format(period="AM"), "r") as am_file:
            if use_old_skims:
                am_vsmtrk_time = am_file.read("TIMEVSM")
                am_smltrk_time = am_file.read("TIMESML")
                am_medtrk_time = am_file.read("TIMEMED")
                am_lrgtrk_time = am_file.read("TIMELRG")
            else:
                am_trk_time = am_file.read("am_trk_time")
                am_lrgtrk_time = am_file.read("am_lrgtrk_time")
        with _emme_tools.OMX(skim_path_tmplt.format(period="MD"), "r") as md_file:
            if use_old_skims:
                md_vsmtrk_time = md_file.read("TIMEVSM")
                md_smltrk_time = md_file.read("TIMESML")
                md_medtrk_time = md_file.read("TIMEMED")
                md_lrgtrk_time = md_file.read("TIMELRG")
            else:
                md_trk_time = md_file.read("md_trk_time")
                md_lrgtrk_time = md_file.read("md_lrgtrk_time")

        # blend truck times
        # compute blended truck time as an average 1/3 AM and 2/3 MD
        #     NOTE: Cube outputs skims\COM_HWYSKIMAM_taz.tpp, skims\COM_HWYSKIMMD_taz.tpp
        #           are in the highway_skims_{period}.omx files in Emme version
        #           with updated matrix names, {period}_trk_time, {period}_lrgtrk_time.
        #           Also, there will no longer be separate very small, small and medium
        #           truck times, as they are assigned together as the same class.
        #           There is only the trk_time.
        if use_old_skims:
            vsmtrk_blend_time = 1 / 3.0 * am_vsmtrk_time + 2 / 3.0 * md_vsmtrk_time
            smltrk_blend_time = 1 / 3.0 * am_smltrk_time + 2 / 3.0 * md_smltrk_time
            medtrk_blend_time = 1 / 3.0 * am_medtrk_time + 2 / 3.0 * md_medtrk_time
        else:
            vsmtrk_blend_time = smltrk_blend_time = medtrk_blend_time = \
                1 / 3.0 * am_trk_time + 2 / 3.0 * md_trk_time
        lrgtrk_blend_time = 1 / 3.0 * am_lrgtrk_time + 2 / 3.0 * md_lrgtrk_time
        ffactors = self._load_ff_lookup_tables()
        k_factors = self._load_k_factors()
        friction_calculations = [
            {"name": "vsmtrk", "time": vsmtrk_blend_time, "use_k_factors": False},
            {"name": "smltrk", "time": smltrk_blend_time, "use_k_factors": True},
            {"name": "medtrk", "time": medtrk_blend_time, "use_k_factors": True},
            {"name": "lrgtrk", "time": lrgtrk_blend_time, "use_k_factors": True},
        ]
        daily_demand = {}
        for spec in friction_calculations:
            name = spec["name"]
            # lookup friction factor values from table with interpolation
            # and multiply by k-factors (no k-factors for very small truck)
            friction_matrix = np.interp(spec["time"], ffactors["time"], ffactors[name])
            if spec["use_k_factors"]:
                friction_matrix = friction_matrix * k_factors
            # run matrix balancing
            prod_attr_matrix = self._matrix_balancing(
                friction_matrix,
                trip_ends[f"{name}_prod"],
                trip_ends[f"{name}_attr"],
                name,
            )
            daily_demand[name] = 0.5 * prod_attr_matrix + 0.5 * prod_attr_matrix.transpose()
            # self.logger.log(name, prod_attr_matrix.sum(), daily_demand[name].sum())
        return daily_demand

    @LogStartEnd()
    def _time_of_day(self, daily_demand):
        """Apply period factors to convert daily demand totals to per-period demands."""
        period_demand = {}
        for period in [p.name for p in self.config.periods]:
            factor_map = _time_of_day_split[period]
            demand = {}
            for name, factor in factor_map.items():
                #demand[name] = factor * daily_demand[name]
                demand[name] = np.around(factor * daily_demand[name], decimals=2)
            period_demand[period] = demand
        return period_demand

    @LogStartEnd()
    def _toll_choice(self, period_demand):
        """Split per-period truck demands into nontoll and toll classes."""
        # input: time-of-day matrices
        # skims: skims\COM_HWYSKIM@token_period@_taz.tpp -> traffic_skims_{period}.omx
        #        NOTE matrix name changes in Emme version, using {period}_{class}_{skim}
        #        format

        truck_vot = self.config.truck.value_of_time
        k_ivtt = self.config.truck.toll_choice_time_coefficient
        op_cost = self.config.truck.operating_cost_per_mile
        k_cost = (k_ivtt / truck_vot) * 0.6
        class_demand = {}
        # TODO: skim names to parameter?
        for period, demands in period_demand.items():
            skim_path_tmplt = os.path.join(self.root_dir, self.config.highway.output_skim_path)
            with _emme_tools.OMX(skim_path_tmplt.format(period=period)) as skims:
                split_demand = {}
                for name, total_trips in demands.items():
                    if use_old_skims:
                        padding = ((0, 4756 - 4735), (0, 4756 - 4735))
                        cls_name = name[:3].upper()
                        nontoll_time = np.pad(skims.read(f"TIME{cls_name}"), padding)
                        nontoll_dist = np.pad(skims.read(f"DIST{cls_name}"), padding)
                        nontoll_bridgecost = np.pad(
                            skims.read(f"BTOLL{cls_name}"), padding
                        )
                        toll_time = np.pad(skims.read(f"TOLLTIME{cls_name}"), padding)
                        toll_dist = np.pad(skims.read(f"TOLLDIST{cls_name}"), padding)
                        toll_bridgecost = np.pad(
                            skims.read(f"TOLLBTOLL{cls_name}"), padding
                        )
                        toll_tollcost = np.pad(
                            skims.read(f"TOLLVTOLL{cls_name}"), padding
                        )
                    else:
                        cls_name = "trk" if name != "lrgtrk" else "lrgtrk"
                        grp_name = name[:3]
                        nontoll_time = skims.read(f"{period}_{cls_name}_time")
                        nontoll_dist = skims.read(f"{period}_{cls_name}_dist")
                        nontoll_bridgecost = skims.read(
                            f"{period}_{cls_name}_bridgetoll{grp_name}"
                        )
                        toll_time = skims.read(f"{period}_{cls_name}toll_time")
                        toll_dist = skims.read(f"{period}_{cls_name}toll_dist")
                        toll_bridgecost = skims.read(
                            f"{period}_{cls_name}toll_bridgetoll{grp_name}"
                        )
                        toll_tollcost = skims.read(
                            f"{period}_{cls_name}toll_valuetoll{grp_name}"
                        )

                    e_util_nontoll = np.exp(
                        k_ivtt * nontoll_time
                        + k_cost * (op_cost * nontoll_dist + nontoll_bridgecost)
                    )
                    e_util_toll = np.exp(
                        k_ivtt * toll_time
                        + k_cost
                        * (op_cost * toll_dist + toll_bridgecost + toll_tollcost)
                    )
                    prob_nontoll = e_util_nontoll / (e_util_toll + e_util_nontoll)
                    prob_nontoll[(toll_tollcost == 0) | (toll_tollcost > 999999)] = 1.0
                    prob_nontoll[(nontoll_time == 0) | (nontoll_time > 999999)] = 0.0
                    split_demand[name] = prob_nontoll * total_trips
                    split_demand[f"{name}toll"] = (1 - prob_nontoll) * total_trips

                class_demand[period] = split_demand
        return class_demand

    @LogStartEnd()
    def _export_results(self, class_demand):
        """Export assignable class demands to OMX files by time-of-day."""
        path_tmplt = os.path.join(self.root_dir, self.config.truck.highway_demand_file)
        os.makedirs(os.path.dirname(path_tmplt), exist_ok=True)
        for period, matrices in class_demand.items():
            with _emme_tools.OMX(path_tmplt.format(period=period), "w") as output_file:
                for name, data in matrices.items():
                    output_file.write_array(data, name)

    def _matrix_balancing(self, friction_matrix, orig_totals, dest_totals, name):
        """Run Emme matrix balancing tool using input arrays."""
        matrix_balancing = self._emme_manager.tool(
            "inro.emme.matrix_calculation.matrix_balancing"
        )
        matrix_round = self._emme_manager.tool(
            "inro.emme.matrix_calculation.matrix_controlled_rounding"
        )
        od_values_name = f"{name}_friction"
        orig_totals_name = f"{name}_prod"
        dest_totals_name = f"{name}_attr"
        result_name = f"{name}_daily_demand"
        # save O-D friction, prod and dest total values to Emmebank matrix
        self._save_to_emme_matrix(od_values_name, friction_matrix)
        self._save_to_emme_matrix(orig_totals_name, orig_totals)
        self._save_to_emme_matrix(dest_totals_name, dest_totals)
        spec = {
            "od_values_to_balance": od_values_name,
            "origin_totals": orig_totals_name,
            "destination_totals": dest_totals_name,
            "allowable_difference": 0.01,
            "max_relative_error": self.config.truck.max_balance_relative_error,
            "max_iterations": self.config.truck.max_balance_iterations,
            "results": {"od_balanced_values": result_name},
            "performance_settings": {
                "allowed_memory": None,
                "number_of_processors": self._num_processors,
            },
            "type": "MATRIX_BALANCING",
        }
        matrix_balancing(spec, scenario=self._scenario)
        matrix_round(
            result_name,
            result_name,
            min_demand=0.01,
            values_to_round="ALL_NON_ZERO",
            scenario=self._scenario
        )
        matrix = self._scenario.emmebank.matrix(result_name)
        return matrix.get_numpy_data(self._scenario.id)

    def _save_to_emme_matrix(self, name, data):
        """Save numpy data to Emme matrix (in Emmebank) with specified name."""
        num_zones = len(self._scenario.zone_numbers)
        # reshape (e.g. pad externals) with zeros
        shape = data.shape
        if shape != [num_zones] * data.ndim:
            padding = [(0, num_zones - dim_shape) for dim_shape in shape]
            data = np.pad(data, padding)
        matrix = self._scenario.emmebank.matrix(name)
        matrix.set_numpy_data(data, self._scenario.id)

    def _load_ff_lookup_tables(self):
        """Load friction factors lookup tables from file [config.truck.friction_factors_file]."""
        #   time is in column 0, very small FF in 1, small FF in 2,
        #   medium FF in 3, and large FF in 4
        factors = {"time": [], "vsmtrk": [], "smltrk": [], "medtrk": [], "lrgtrk": []}
        file_path = os.path.join(self.root_dir, self.config.truck.friction_factors_file)
        with open(file_path, "r") as truck_ff:
            for line in truck_ff:
                tokens = line.split()
                for key, token in zip(factors.keys(), tokens):
                    factors[key].append(float(token))
        return factors

    def _load_k_factors(self):
        """Load k-factors table from CSV file [config.truck.k_factors_file]."""
        # NOTE: loading from this text format to numpy is pretty slow (~10 seconds)
        #       would be better to use a different format
        data = pd.read_csv(os.path.join(self.root_dir, self.config.truck.k_factors_file))
        zones = np.unique(data["I_taz_tm2_v2_2"])
        num_data_zones = len(zones)
        row_index = np.searchsorted(zones, data["I_taz_tm2_v2_2"])
        col_index = np.searchsorted(zones, data["J_taz_tm2_v2_2"])
        k_factors = np.zeros((num_data_zones, num_data_zones))
        k_factors[row_index, col_index] = data["truck_k"]
        num_zones = len(self._scenario.zone_numbers)
        padding = ((0, num_zones - num_data_zones), (0, num_zones - num_data_zones))
        k_factors = np.pad(k_factors, padding)
        return k_factors
