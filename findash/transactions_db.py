from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import pandas as pd

from categories_db import CategoriesDB
from utils import SETTINGS, create_uuid, format_date_col_for_display

"""
The purpose of this module is to provide a database for transactions.
The database in a collection of parquet files, one per month, organized by year.
This is the recorded history of the transactions.
"""


@dataclass
class TransDBSchema:
    ID: str = 'id'
    DATE: datetime = 'date'
    PAYEE: str = 'payee'
    CAT: pd.CategoricalDtype = 'cat'
    CAT_GROUP: pd.CategoricalDtype = 'cat_group'
    MEMO: str = 'memo'
    ACCOUNT: pd.CategoricalDtype = 'account'
    INFLOW: float = 'inflow'  # if forex trans will show the conversion to ils here
    OUTFLOW: float = 'outflow'  # if forex trans will show the conversion to ils here
    RECONCILED: bool = 'reconciled'
    AMOUNT: float = 'amount'  # can be in forex

    @classmethod
    def get_mandatory_cols(cls) -> tuple:
        """
        mandatory cols every raw transactions file must have
        """
        return cls.DATE, cls.PAYEE, cls.AMOUNT

    @classmethod
    def get_non_mandatory_cols(cls) -> Dict[str, Any]:
        """
        dictionary of non-mandatory cols (keys) to add to trans file to align with
        DB schema along with default values (values)
        """
        return {cls.CAT: '',
                cls.CAT_GROUP: '',
                cls.MEMO: '',
                cls.ACCOUNT: None,
                cls.INFLOW: 0,
                cls.OUTFLOW: 0,
                cls.RECONCILED: False}

    @classmethod
    def get_db_col_names(cls):
        return [f.name for f in fields(cls)]

    @classmethod
    def get_db_col_vals(cls):
        return [f.default for f in fields(cls)]

    @classmethod
    def get_db_col_dict(cls):
        return dict(zip(cls.get_db_col_names(), cls.get_db_col_vals()))

    @classmethod
    def get_numeric_cols(cls):
        return [cls.INFLOW, cls.OUTFLOW, cls.AMOUNT]

    @classmethod
    def get_displayed_cols_by_type(cls):
        return {
            'date': [cls.DATE],
            'str': [cls.PAYEE, cls.MEMO],
            'numeric': [cls.INFLOW, cls.OUTFLOW, cls.AMOUNT],
            'cat': [cls.CAT, cls.ACCOUNT],
            'readonly': [cls.CAT_GROUP]
        }

    @classmethod
    def get_dropdown_cols(cls):
        return [cls.CAT, cls.ACCOUNT]

    @classmethod
    def get_non_special_cols(cls):
        return [cls.PAYEE, cls.MEMO, cls.INFLOW, cls.OUTFLOW, cls.AMOUNT]

    @classmethod
    def get_date_cols(cls):
        return [cls.DATE]


class TransactionsDBParquet:
    def __init__(self, cat_db: CategoriesDB, db: pd.DataFrame = pd.DataFrame()):
        self._db: pd.DataFrame = db
        self._cat_db = cat_db

    def __getitem__(self, item):
        return TransactionsDBParquet(self._cat_db, self._db.__getitem__(item))

    def __getattr__(self, item):
        return self._db.__getattr__(item)

    def __setitem__(self, name, value):
        return TransactionsDBParquet(self._cat_db,
                                     self._db.__setitem__(name, value))

    def __eq__(self, other):
        return self._db.__eq__(other)

    def __ge__(self, other):
        return self._db.__ge__(other)

    def __le__(self, other):
        return self._db.__le__(other)

    def __repr__(self):
        return self._db.__repr__()

    def connect(self, db_path: str):
        """
        load parquet files of transactions
        :param db_path: path to db root folder
        :return:
        """
        root_path = Path(db_path)

        pq_files = []
        for item in root_path.glob('*'):
            if item.is_dir():
                for file in item.iterdir():
                    pq_files.append(pd.read_parquet(file))
            else:
                if item.name.endswith('pq'):
                    pq_files.append(pd.read_parquet(item))

        if len(pq_files) == 0:
            self._db = pd.DataFrame()

        final_df = pd.concat(pq_files)
        final_df = apply_dtypes(final_df, include_date=False)
        self._db = final_df
        self._sort_by_date()

    def disconnect(self):
        """
        In the case of a parquet db, disconnecting will only save the db
        """
        raise NotImplementedError(
            'disconnecting from a parquet db is not implemented')

    def save_db(self, months_to_save: List[Tuple[str, str]]) -> None:
        """
        save the db to a parquet file. Saves only modified months
        :param months_to_save: list of tuples of form (year, month)
        :return:
        """
        # if len(months_to_save) == 0:
        #     self._save_no_date_db()

        trans_db_path = Path(SETTINGS['db']['trans_db_path'])
        for year, month in months_to_save:
            year_dir = trans_db_path / str(year)
            if not year_dir.exists():
                year_dir.mkdir()

            cond1 = self._db[TransDBSchema.DATE].dt.year == int(year)
            cond2 = self._db[TransDBSchema.DATE].dt.month == int(month)
            self._db[cond1 & cond2].to_parquet(year_dir / f'{month}.pq')

    def save_db_from_uuids(self, uuid_list: List[str]) -> None:
        """
        given a list of uuids, extracts the transaction months and saves the relevant parquet
        files
        :param uuid_list:
        :return:
        """
        months = self._get_months_from_uuid(uuid_list)
        self.save_db(months)

    # def _save_no_date_db(self,) -> None:
    #     """
    #     saves transactions with no date to a parquet file for transactions with
    #      no date
    #     """
    #     self._db[self._db[TransDBSchema.DATE].isnull()].to_parquet(
    #         Path(SETTINGS['db']['trans_db_path']) / 'no_date.pq')

    def get_data_by_group(self, group: str):
        """
        get data by group
        :param group: group to get data from
        :return: dataframe of data
        """
        return TransactionsDBParquet(self._cat_db,
            self._db[self._db[TransDBSchema.CAT_GROUP] == group])

    def get_data_by_cat(self, cat: str) -> pd.DataFrame:
        """
        get data by category
        :param cat: category to get data from
        :return: dataframe of data
        """
        return self._db[self._db[TransDBSchema.CAT] == cat]

    def get_data_by_id(self, uuid_list: List[str]) -> pd.DataFrame:
        """
        get transactions by id
        :param uuid_list: list of uuids
        :return: dataframe of transactions
        """
        return self._db[self._db[TransDBSchema.ID].isin(uuid_list)]

    def get_data_by_col_val(self,
                            col_val_dict: Dict[str, Any]) -> pd.DataFrame:
        """
        get transactions by column value - supports only intersection of values.
        :param col_val_dict: dict where the keys are the columns and the values
               are the values in the columns. Supports only one value per column
        :return: dataframe of transactions
        """
        db_tmp = self._db
        for col, val in col_val_dict.items():
            db_tmp = db_tmp[db_tmp[col] == val]

        return db_tmp

    def get_current_month_trans(self):
        """
        get data of current month
        :return: dataframe of data
        """
        current_month = datetime.now().strftime('%Y-%m')
        curr_trans = self._db[self._db[TransDBSchema.DATE].dt.strftime('%Y-%m')
                              == current_month]
        return TransactionsDBParquet(self._cat_db, curr_trans)

    def get_records(self) -> dict:
        """
        get records of db to feed into dash datatable
        :return:
        """
        formatted_df = format_date_col_for_display(self._db,
                                                   TransDBSchema.DATE)
        return formatted_df.to_dict('records')

    def insert_data(self, df: pd.DataFrame) -> None:
        """
        insert transactions to the db
        :param df: dataframe of transactions
        :return:
        """
        df = self._add_uuids(df)
        df = self._apply_categories(df)
        self._db = pd.concat([self._db, df])
        self._sort_by_date()
        self._db = self._db.reset_index(drop=True)
        self.save_db_from_uuids(df[TransDBSchema.ID].to_list())

    def _sort_by_date(self):
        """
        sort the db by date
        :return:
        """
        self._db = self._db.sort_values(by=TransDBSchema.DATE, ascending=False)
        self._db = self._db.reset_index(drop=True)

    def _apply_categories(self, df: pd.DataFrame):
        """
        add categories to new inserted transactions
        :param df: new transactions
        :return:
        """
        for ind, row in df.iterrows():
            payee = row[TransDBSchema.PAYEE]
            cat, group = self._cat_db.get_cat_and_group_by_payee(payee)
            if cat is not None:
                df.iloc[ind, TransDBSchema.CAT] = cat
                df.iloc[ind, TransDBSchema.CAT_GROUP] = group

        return df

    def add_blank_row(self):
        """
        when adding a new transaction, add a blank row to the db which will
        probably be edited and populated later
        :return:
        """
        uuid = create_uuid()
        num_cols_wo_id = self._db.shape[1] - 1
        blank_row = pd.DataFrame([uuid] + [None] * num_cols_wo_id,
                                 index=self._db.columns)
        self._db = pd.concat([blank_row.T, self._db])

        self.save_db_from_uuids([uuid])

    def remove_row_with_id(self, id: str):
        """
        remove row with id
        :param id: id of row to remove
        :return:
        """
        months = self._get_months_from_uuid([id])
        self._db = self._db[self._db[TransDBSchema.ID] != id]
        self.save_db(months)

    def update_data(self, col_name: str, index: int, value: Any) -> None:
        # self._fix_extra_trans_bug()
        if col_name == TransDBSchema.CAT:
            if value not in self._db[TransDBSchema.CAT].cat.categories:
                self._db[TransDBSchema.CAT] = self._db[TransDBSchema.CAT].cat.\
                    add_categories(value)

        prev_value = self._db.loc[index, col_name]
        self._db.loc[index, col_name] = value

        # trans moved to another month - save original month to save removal
        if isinstance(prev_value, pd.Timestamp):
            if prev_value.month != pd.to_datetime(value).month:
                self.save_db([(str(prev_value.year), str(prev_value.month))])

        uuid_list = [self._db.loc[index, TransDBSchema.ID]]

        if col_name == TransDBSchema.DATE:
            self._sort_by_date()

        self.save_db_from_uuids(uuid_list)

    # def _fix_extra_trans_bug(self):
    #     """
    #     there is weird bug that adds an empty transaction to the db on startup
    #     this function removes it
    #     :return:
    #     """
    #     self._db = self._db[~self._db[TransDBSchema.DATE].isnull()]

    def _get_months_from_uuid(self, uuid_lst: List[str]) -> List[
        Tuple[str, str]]:
        """
        get the months of the transactions with the given uuids
        :return: a set of lists of form [year, month]
        """
        months = set()
        for uuid in uuid_lst:
            date = self._db[self._db[TransDBSchema.ID] == uuid][
                TransDBSchema.DATE]
            if date.isnull().any():
                return []

            date = date.iloc[0]
            months.add((date.year, date.month))

        return list(months)

    @staticmethod
    def _add_uuids(df: pd.DataFrame) -> pd.DataFrame:
        """
        add uuids to the transactions
        :param df: dataframe of transactions
        :return: dataframe of transactions with uuids
        """
        # TODO: maybe vectorize the uuid creation
        df[TransDBSchema.ID] = df.apply(lambda x: create_uuid(), axis=1)

        return df

    @property
    def db(self):
        return self._db


def apply_dtypes(df: pd.DataFrame, include_date: bool = True,
                 datetime_format: Optional[str] = None) -> pd.DataFrame:
    """
    apply the dtypes of the db schema to the dataframe
    :param df: dataframe to apply dtypes to
    :param include_date: whether to include date column
    :param datetime_format: format of the date column according to input
                            trans file
    :return: dataframe with dtypes applied
    """
    if include_date:
        df[TransDBSchema.DATE] = pd.to_datetime(df[TransDBSchema.DATE],
                                                format=datetime_format)
    df[TransDBSchema.RECONCILED] = df[TransDBSchema.RECONCILED].astype(
        bool)
    df[TransDBSchema.INFLOW] = df[TransDBSchema.INFLOW].astype(float)
    df[TransDBSchema.OUTFLOW] = df[TransDBSchema.OUTFLOW].astype(float)
    df[TransDBSchema.AMOUNT] = df[TransDBSchema.AMOUNT].astype(float)
    df[TransDBSchema.CAT] = df[TransDBSchema.CAT].astype('category')
    df[TransDBSchema.CAT_GROUP] = df[TransDBSchema.CAT_GROUP]. \
        astype('category')
    df[TransDBSchema.ACCOUNT] = df[TransDBSchema.ACCOUNT].astype(
        'category')
    df[TransDBSchema.RECONCILED] = df[TransDBSchema.RECONCILED].astype(
        bool)

    return df
